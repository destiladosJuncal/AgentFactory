"""
Encryption at rest for the capture headers.

## What problem it solves, and which one it doesn't

The capture database used to store headers in plain text, with the session
cookies and the full `Authorization`. Any script could deduce the path —the
child process inherits `AGENTE_DATOS`— run a `SELECT` and carry the credentials
into the prompt, and from there to the model provider.

With this, that `SELECT` returns unreadable bytes. The **accidental** exposure
goes away: reading the table is no longer enough.

What it does NOT solve, stated plainly: the agent runs code as the same user, so
it can reach the key file just like the app can. The defense isn't
cryptographic, it's about **consent**: decrypting goes through the same dialog as
a destructive command, with "allow once" or "allow always" per conversation. The
goal is that accessing credentials be an act that's seen and approved, not a side
effect of processing the capture.

For the stolen-disk case the right tool is BitLocker or FileVault, not this.

## Details

  · Fernet (AES-128-CBC + HMAC), shipped with `cryptography`, already a
    dependency of mitmproxy. Nothing new is added to the portable bootstrap.
  · The key lives with the DATA, never with the code, so a zip meant to be
    shared doesn't carry it — same as the `.env`.
  · Values that were already in plain text still read fine: `decrypt` returns
    them as-is when they lack the prefix. That way an old capture doesn't break
    and nothing has to be migrated up front.
"""

import base64
from pathlib import Path
from typing import Optional

# Marker at the start of the encrypted value. It serves two purposes:
# telling encrypted values apart from ones left in plain text from before, and
# letting a human who opens the database understand what they're seeing instead
# of thinking it got corrupted.
PREFIX = "enc:v1:"

KEY_FILENAME = "clave-captura.key"

_cache = {}


def key_path() -> Path:
    from core.rutas import dir_datos
    return dir_datos() / KEY_FILENAME


def _protect(path: Path):
    """Restrictive permissions where the system honors them."""
    from core.rutas import proteger
    proteger(path)


def key() -> Optional[bytes]:
    """This installation's key. Created the first time it's used.

    Returns None when it can't be had (`cryptography` missing, or the data
    folder isn't writable). In that case the rest degrades to plain text instead
    of losing the capture: a deliberate decision — keeping the app working
    matters more than encrypting at all costs, and the real state shows in the
    Diagnostics panel."""
    path = key_path()
    cached = _cache.get("key")
    if cached is not None and _cache.get("path") == str(path):
        return cached

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None

    try:
        if path.exists():
            material = path.read_bytes().strip()
        else:
            material = Fernet.generate_key()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(material)
            _protect(path)
    except OSError:
        return None

    # A corrupt key is remade: losing the old capture is bad, but leaving the
    # app unusable is worse, and without a valid key nothing can be done with
    # what's already stored.
    try:
        Fernet(material)
    except Exception:
        try:
            material = Fernet.generate_key()
            path.write_bytes(material)
            _protect(path)
        except OSError:
            return None

    _cache["key"] = material
    _cache["path"] = str(path)
    return material


def available() -> bool:
    return key() is not None


def forget():
    """Clears the cache. For the tests and for when AGENTE_DATOS changes."""
    _cache.clear()


def encrypt(text: str) -> str:
    """Returns the text encrypted with the prefix, or as-is if it can't."""
    if not text:
        return text or ""
    material = key()
    if material is None:
        return text
    try:
        from cryptography.fernet import Fernet
        token = Fernet(material).encrypt(text.encode("utf-8"))
        return PREFIX + base64.b64encode(token).decode("ascii")
    except Exception:
        return text


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def decrypt(value: str) -> str:
    """Decrypts if needed. Anything not encrypted comes back unchanged.

    NOTE: this function asks nothing. Consent is requested at the edge —where the
    agent asks for unredacted content, or where a script references the store—
    because down here also pass the internal uses (the session fingerprint, the
    flow grouping, the proxy tab) that show the value to no one and would have no
    reason to bother the person."""
    if not is_encrypted(value):
        return value
    material = key()
    if material is None:
        return ""
    try:
        from cryptography.fernet import Fernet
        raw = base64.b64decode(value[len(PREFIX):].encode("ascii"))
        return Fernet(material).decrypt(raw).decode("utf-8")
    except Exception:
        # Changed key or corrupt data: return empty and don't blow up. An
        # unreadable header degrades the analysis; an exception here would break
        # the proxy tab and the session grouping.
        return ""


def estado() -> dict:
    """For the Diagnostics panel."""
    path = key_path()
    from core import plataforma
    permissions = None
    if path.exists():
        try:
            permissions = oct(path.stat().st_mode & 0o777)
        except OSError:
            pass
    return {
        "encrypted": available(),
        "key_path": str(path),
        "key_exists": path.exists(),
        "key_permissions": permissions,
        "permissions_apply": plataforma.soporta_permisos_posix(),
    }
