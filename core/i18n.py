"""
Tiny internationalization layer for the UI and the system prompt.

Why it exists: the whole app was written in Spanish, and we want it to be able
to speak either Spanish or English WITHOUT forking every screen. Instead of
translating strings in place (which would just swap one hardcoded language for
another), user-facing text moves behind ``t("some.key")`` and the actual words
live in the catalog below, one entry per language.

Design choices that matter:

  · **Spanish stays the default.** Existing users keep the app exactly as it is
    until they opt into English, so migrating a screen to ``t()`` is safe even
    before the English wording is polished.
  · **A missing key never crashes.** If a key or a translation is absent, ``t``
    falls back to the default language and, failing that, returns the key
    itself. A half-migrated screen degrades to visible keys, not a stack trace.
  · **No import of config/rutas.** This module is imported very early and very
    widely; reading the environment directly keeps it dependency-free and
    cycle-proof. The language is resolved from ``AGENTE_LANG`` (or the legacy
    ``AGENTE_IDIOMA``), which ``rutas.cargar_env`` has already loaded from the
    ``.env`` by the time any UI runs.

The catalog starts nearly empty on purpose: screens move into it as each one is
migrated (see the i18n phase of the translation plan), not all at once.
"""

import os
from typing import Dict, Optional

DEFAULT_LANG = "es"
SUPPORTED = ("es", "en")

# Set by the UI's language switch at runtime; overrides the environment. None
# means "no explicit choice, fall back to the environment / default".
_override: Optional[str] = None


def current_lang() -> str:
    """The language in effect: runtime override, else env, else the default."""
    if _override in SUPPORTED:
        return _override
    for var in ("AGENTE_LANG", "AGENTE_IDIOMA"):
        code = os.environ.get(var, "").strip().lower()
        if code in SUPPORTED:
            return code
    return DEFAULT_LANG


def set_lang(code: Optional[str]) -> None:
    """Pick the language for this run. ``None`` clears the override so the
    environment/default decides again. Unknown codes are ignored (stay on the
    previous choice) rather than silently switching to a language we can't
    render."""
    global _override
    if code is None or code in SUPPORTED:
        _override = code


# key -> {lang: text}. Placeholders use str.format syntax, e.g. "{n} flujos".
MESSAGES: Dict[str, Dict[str, str]] = {
    "captura.tamano": {
        "es": "📦 captura: {tamano}",
        "en": "📦 capture: {tamano}",
    },
    "captura.vacia": {
        "es": "📦 captura: vacía",
        "en": "📦 capture: empty",
    },
}


def t(key: str, lang: Optional[str] = None, **kw) -> str:
    """Translate ``key`` into ``lang`` (default: the current language).

    Falls back to the default language, then to the raw key, so a missing entry
    is visible but never fatal. ``kw`` are interpolated with ``str.format``; a
    bad placeholder returns the unformatted text instead of raising."""
    lang = lang if lang in SUPPORTED else current_lang()
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text
