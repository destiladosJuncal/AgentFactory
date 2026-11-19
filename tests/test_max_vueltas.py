# -*- coding: utf-8 -*-
"""El tope de vueltas de herramientas es configurable (AGENTE_MAX_VUELTAS),
se lee por turno y se acota a un rango sano."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import chat  # noqa: E402


def test_default_cuando_no_esta_seteado(monkeypatch):
    monkeypatch.delenv("AGENTE_MAX_VUELTAS", raising=False)
    assert chat._max_vueltas() == chat.MAX_ITERACIONES_TOOLS


def test_respeta_el_valor_configurado(monkeypatch):
    monkeypatch.setenv("AGENTE_MAX_VUELTAS", "35")
    assert chat._max_vueltas() == 35


def test_valor_vacio_cae_al_default(monkeypatch):
    monkeypatch.setenv("AGENTE_MAX_VUELTAS", "   ")
    assert chat._max_vueltas() == chat.MAX_ITERACIONES_TOOLS


def test_valor_no_numerico_cae_al_default(monkeypatch):
    monkeypatch.setenv("AGENTE_MAX_VUELTAS", "muchas")
    assert chat._max_vueltas() == chat.MAX_ITERACIONES_TOOLS


def test_se_acota_el_rango(monkeypatch):
    monkeypatch.setenv("AGENTE_MAX_VUELTAS", "1")
    assert chat._max_vueltas() == 3            # piso
    monkeypatch.setenv("AGENTE_MAX_VUELTAS", "99999")
    assert chat._max_vueltas() == 100          # techo


def test_el_prompt_de_vueltas_refleja_el_numero():
    assert "25" in chat.BLOQUE_VUELTAS.format(n=25)
    # El número ya no está hardcodeado en el prompt base.
    assert "vueltas de herramientas" not in chat.SYSTEM_PROMPT_BASE
