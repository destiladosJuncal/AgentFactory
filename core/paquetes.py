"""
Python packages shared across conversations.

The problem it solves: every conversation that needed numpy/scipy/cv2 built its
own venv inside the workspace. Ten conversations with the same heavy libraries =
ten copies of hundreds of MB each.

The solution is a SHARED store and a LOCAL overlay only when needed:

    $DATOS/_paquetes/py3.12/          <- shared: installed ONCE
    <workspace>/.paquetes/py3.12/     <- local: only on a version conflict

At run time, PYTHONPATH is built as [local, shared]: local wins. So a
conversation that needs numpy==1.26 doesn't break the others using 2.x, and the
rest of the libraries are still shared all the same.

Split by Python version (py3.12, py3.9...) on purpose: wheels with C extensions
aren't compatible across versions, and mixing them gives import errors that are
impossible to diagnose.

(The LLM-facing tool names and schema descriptions, the returned dict keys and
the `permisos` parameter stay Spanish on purpose: the tool names are part of the
conversation-history contract, and `permisos` threads through the permission
system — both move in their own later phase.)
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import interprete

INSTALL_TIMEOUT = 600   # installing scipy/torch takes a while


def _python_tag() -> str:
    return f"py{sys.version_info.major}.{sys.version_info.minor}"


def shared_dir() -> Path:
    """Store shared by ALL conversations."""
    from core.rutas import dir_datos
    d = dir_datos() / "_paquetes" / _python_tag()
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_dir(workspace) -> Optional[Path]:
    """This conversation's overlay. Only used on a version conflict."""
    if not workspace:
        return None
    return Path(workspace) / ".paquetes" / _python_tag()


def normalize(name: str) -> str:
    """Canonical name per PEP 503 ('Pillow' and 'pillow' are the same package)."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def installed(directory) -> Dict[str, str]:
    """{normalized_name: version} reading the *.dist-info of the directory."""
    found: Dict[str, str] = {}
    if not directory:
        return found
    d = Path(directory)
    if not d.is_dir():
        return found
    for info in d.glob("*.dist-info"):
        stem = info.name[: -len(".dist-info")]
        if "-" not in stem:
            continue
        name, _, version = stem.rpartition("-")
        found[normalize(name)] = version
    return found


def _parse(requirement: str):
    """(normalized_name, specifier) or (name, None) if it can't be parsed."""
    try:
        from packaging.requirements import Requirement
        r = Requirement(requirement)
        return normalize(r.name), r.specifier
    except Exception:
        name = re.split(r"[<>=!~\[ ]", (requirement or "").strip(), 1)[0]
        return normalize(name), None


def _satisfies(version: str, specifier) -> bool:
    """Does the installed version satisfy what was asked? Without a specifier, yes."""
    if specifier is None or not str(specifier):
        return True
    try:
        from packaging.version import Version
        return specifier.contains(Version(version), prereleases=True)
    except Exception:
        return False


def pythonpath(workspace=None) -> str:
    """PYTHONPATH with local first and shared after."""
    parts: List[str] = []
    local = local_dir(workspace)
    if local and local.is_dir():
        parts.append(str(local))
    parts.append(str(shared_dir()))
    inherited = os.environ.get("PYTHONPATH", "")
    if inherited:
        parts.append(inherited)
    return os.pathsep.join(parts)


def environment(workspace=None) -> Dict[str, str]:
    """Copy of the environment with the store's PYTHONPATH set."""
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath(workspace)
    # Without this, a script that prints '→' with stdout captured in a pipe uses
    # the local encoding (cp1252 on Windows) and dies with UnicodeEncodeError
    # before doing anything. Harmless on macOS, and here it covers both
    # ejecutar_python and ejecutar_shell in one place.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _pip_install(requirement: str, destination: Path) -> Tuple[bool, str]:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [interprete.interpreter(), "-m", "pip", "install", "--target", str(destination),
             "--upgrade", requirement],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"La instalación superó los {INSTALL_TIMEOUT}s"
    except Exception as e:
        return False, f"No pude ejecutar pip: {e}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[-600:]
    return True, (proc.stdout or "").strip()[-300:]


def _confirm_install(requirement: str, name: str, where: str,
                     permisos) -> Any:
    """Asks the person before pulling third-party code from PyPI.

    We only get here when there's really something to install: if the package is
    already in the shared store or the local one, install() returns earlier and
    asks nothing. That's why the confirmation shows up rarely and, when it does,
    it matters.

    The key is a single one for the whole conversation ('pip'), so "always allow"
    enables the installs that come afterwards in that same conversation. It's
    what's wanted: when the agent is setting up an environment it usually needs
    several packages in a row and asking one by one is noise.
    """
    from core import ejecucion
    detail = (f"Paquete: {requirement}\n"
              f"Destino: instalación {where}\n\n"
              f"Se va a ejecutar pip install, que descarga y ejecuta código de "
              f"PyPI. Revisá que el nombre sea el que esperabas: un nombre "
              f"parecido al de un paquete conocido puede ser otro paquete.")
    return ejecucion.pedir_permiso_simple(
        "pip", f"instalar el paquete '{name}' desde PyPI", detail, permisos)


def install(requirement: str, workspace=None,
             permisos=None) -> Dict[str, Any]:
    """Installs a package reusing the shared store whenever possible.

    - If it's already there (shared or local) and the version works: downloads nothing.
    - If it's NOT there: goes to the shared store, so the next conversation reuses it.
    - If it's there but the requested version clashes with the shared one: it's
      installed ONLY for this conversation, without touching what the others use.
    """
    requirement = (requirement or "").strip()
    if not requirement:
        return {"error": "Falta el nombre del paquete (ej: 'numpy' o 'numpy==1.26.4')"}

    name, specifier = _parse(requirement)
    shared, local = shared_dir(), local_dir(workspace)

    in_local = installed(local)
    if name in in_local and _satisfies(in_local[name], specifier):
        return {"estado": "ya_estaba", "donde": "local", "paquete": name,
                "version": in_local[name], "ahorro": "no se descargó nada"}

    in_shared = installed(shared)
    if name in in_shared:
        if _satisfies(in_shared[name], specifier):
            return {"estado": "ya_estaba", "donde": "compartida", "paquete": name,
                    "version": in_shared[name], "ahorro": "no se descargó nada"}
        # Real conflict: the shared one is no good for this conversation.
        if local is None:
            return {"error": f"'{name}' compartido está en {in_shared[name]}, "
                             f"que no cumple '{requirement}', y no hay workspace para "
                             f"instalarlo aparte."}
        block = _confirm_install(requirement, name, "local", permisos)
        if block:
            return block
        ok, detail = _pip_install(requirement, local)
        if not ok:
            return {"error": f"No pude instalar '{requirement}' en local: {detail}"}
        return {"estado": "instalado", "donde": "local", "paquete": name,
                "version": installed(local).get(name, "?"),
                "motivo": (f"conflicto de versión: la compartida es "
                           f"{in_shared[name]} y pediste '{requirement}'. Se "
                           f"instaló solo para esta conversación.")}

    block = _confirm_install(requirement, name, "compartida", permisos)
    if block:
        return block
    ok, detail = _pip_install(requirement, shared)
    if not ok:
        return {"error": f"No pude instalar '{requirement}': {detail}"}
    return {"estado": "instalado", "donde": "compartida", "paquete": name,
            "version": installed(shared).get(name, "?"),
            "nota": "queda disponible para todas las conversaciones"}


def list_installed(workspace=None) -> Dict[str, Any]:
    shared = installed(shared_dir())
    local = installed(local_dir(workspace))
    return {
        "compartidas": dict(sorted(shared.items())),
        "locales_de_esta_conversacion": dict(sorted(local.items())),
        "ruta_compartida": str(shared_dir()),
    }


# --- Chat tool --------------------------------------------------------------

def ejecutar_tool_paquetes(nombre: str, argumentos: dict, workspace=None,
                           permisos=None) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "instalar_paquete":
        return install(a.get("paquete", ""), workspace=workspace, permisos=permisos)
    if nombre == "listar_paquetes":
        return list_installed(workspace=workspace)
    return {"error": f"Herramienta de paquetes desconocida: {nombre}"}


TOOLS_SCHEMA_PAQUETES: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "instalar_paquete",
            "description": (
                "Instala una biblioteca de Python para poder usarla. USALA SIEMPRE "
                "en vez de 'pip install' por shell o de crear un venv: los paquetes "
                "van a un almacén COMPARTIDO entre conversaciones, así no se "
                "vuelven a descargar ni ocupan disco de nuevo. Si la versión que "
                "pedís choca con la compartida, se instala sola para esta "
                "conversación, automáticamente."),
            "parameters": {
                "type": "object",
                "properties": {
                    "paquete": {"type": "string",
                                "description": "Requisito pip: 'numpy', 'numpy==1.26.4', 'pandas>=2'"},
                },
                "required": ["paquete"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_paquetes",
            "description": ("Lista las bibliotecas de Python ya instaladas: las "
                            "compartidas entre conversaciones y las locales de esta. "
                            "Miralas antes de instalar algo."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
