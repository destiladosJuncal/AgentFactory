# -*- coding: utf-8 -*-
"""'Permitir siempre' en el uso de secretos: debe recordarse por secreto y no
volver a preguntar. (Antes NO se guardaba y preguntaba cada vez.)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ejecucion, boveda  # noqa: E402


class Confirmador:
    """Cuenta cuántas veces se preguntó y responde lo programado."""
    def __init__(self, decision):
        self.decision = decision
        self.veces = 0

    def __call__(self, resumen, detalle, clave):
        self.veces += 1
        return self.decision


def _mock_boveda(monkeypatch, etiqueta="API_X"):
    monkeypatch.setattr(boveda, "etiquetas_en",
                        lambda t: [etiqueta] if "{{secreto:" in t else [])
    monkeypatch.setattr(boveda, "tiene", lambda e: True)
    monkeypatch.setattr(boveda, "sustituir", lambda t: (t.replace("{{secreto:%s}}" % etiqueta, "VALOR_REAL"), [etiqueta], []))


def test_siempre_no_vuelve_a_preguntar(monkeypatch):
    _mock_boveda(monkeypatch)
    conf = Confirmador("siempre")
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", conf)
    perm = set()
    texto = "curl -H 'Authorization: {{secreto:API_X}}' https://api"

    real1, err1 = ejecucion._preparar_secretos(texto, perm)
    assert err1 is None and "VALOR_REAL" in real1
    assert "secreto:API_X" in perm          # quedó recordado
    assert conf.veces == 1

    # Segundo uso del MISMO secreto en la misma conversación: no pregunta.
    real2, err2 = ejecucion._preparar_secretos(texto, perm)
    assert err2 is None and "VALOR_REAL" in real2
    assert conf.veces == 1                   # NO volvió a preguntar


def test_permitir_una_vez_no_se_recuerda(monkeypatch):
    _mock_boveda(monkeypatch)
    conf = Confirmador("permitir")
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", conf)
    perm = set()
    texto = "echo {{secreto:API_X}}"

    ejecucion._preparar_secretos(texto, perm)
    ejecucion._preparar_secretos(texto, perm)
    assert conf.veces == 2                   # "una vez" sí pregunta cada vez
    assert "secreto:API_X" not in perm


def test_rechazo_no_sustituye(monkeypatch):
    _mock_boveda(monkeypatch)
    conf = Confirmador("no")
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", conf)
    real, err = ejecucion._preparar_secretos("echo {{secreto:API_X}}", set())
    assert err is not None and "No autorizaste" in err["error"]
    assert "VALOR_REAL" not in real          # no se expuso el valor


def test_un_secreto_nuevo_igual_pregunta(monkeypatch):
    # Con A ya aprobado siempre, un texto que suma B (no aprobado) debe preguntar.
    monkeypatch.setattr(boveda, "etiquetas_en", lambda t: [e for e in ("A", "B") if ("{{secreto:%s}}" % e) in t])
    monkeypatch.setattr(boveda, "tiene", lambda e: True)
    monkeypatch.setattr(boveda, "sustituir", lambda t: (t, ["A", "B"], []))
    conf = Confirmador("permitir")
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", conf)
    perm = {"secreto:A"}
    ejecucion._preparar_secretos("x {{secreto:A}} {{secreto:B}}", perm)
    assert conf.veces == 1                   # B no estaba aprobado -> preguntó
