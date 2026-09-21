"""Tests for the i18n scaffold (core/i18n.py)."""

import os

from core import i18n


def _reset():
    i18n.set_lang(None)
    os.environ.pop("AGENTE_LANG", None)
    os.environ.pop("AGENTE_IDIOMA", None)


def test_default_is_spanish():
    _reset()
    assert i18n.current_lang() == "es"
    assert i18n.t("captura.vacia") == "📦 captura: vacía"


def test_runtime_override_wins_over_env():
    _reset()
    os.environ["AGENTE_LANG"] = "es"
    i18n.set_lang("en")
    try:
        assert i18n.current_lang() == "en"
        assert i18n.t("captura.vacia") == "📦 capture: empty"
    finally:
        _reset()


def test_env_selects_language():
    _reset()
    os.environ["AGENTE_LANG"] = "en"
    try:
        assert i18n.current_lang() == "en"
    finally:
        _reset()


def test_legacy_env_var_is_honored():
    _reset()
    os.environ["AGENTE_IDIOMA"] = "en"
    try:
        assert i18n.current_lang() == "en"
    finally:
        _reset()


def test_unknown_code_is_ignored():
    _reset()
    i18n.set_lang("fr")  # unsupported: must not switch
    assert i18n.current_lang() == "es"


def test_placeholder_interpolation():
    _reset()
    assert i18n.t("captura.tamano", tamano="12 MB") == "📦 captura: 12 MB"


def test_missing_key_returns_key():
    _reset()
    assert i18n.t("no.existe") == "no.existe"


def test_explicit_lang_argument():
    _reset()
    assert i18n.t("captura.vacia", lang="en") == "📦 capture: empty"
    # override still Spanish by default
    assert i18n.t("captura.vacia") == "📦 captura: vacía"
