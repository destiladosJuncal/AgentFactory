"""Tests del conector de clasificación (core.classifier): Jev + fallback LLM.

Se mockea la API de Jev (requests.post) y el proveedor LLM, así corre sin red ni
API. Validamos los builders tipados, la normalización por tipo, el manejo de
errores, el fallback y la decisión de ruteo (confianza baja -> orquestar)."""

import json
import pytest

from core import classifier as clf


# --- builders y normalización ----------------------------------------------

def test_builders():
    c = clf.choice("¿tono?", {"a": "x", "b": "y"})
    assert c["type"] == "choice" and c["criteria"] == {"a": "x", "b": "y"}
    s = clf.score("¿urgencia?", ["bajo", "alto"])
    assert s["type"] == "score" and s["criteria"] == ["bajo", "alto"]
    b = clf.boolean("¿es X?")
    assert b["type"] == "noul"


def test_normalize_choice_score_noul():
    ch = clf._normalize({"type": "choice", "choice": "calm", "confidence": 0.7,
                         "probabilities": {"calm": 0.8, "angry": 0.2}})
    assert ch["value"] == "calm" and ch["confidence"] == 0.7
    sc = clf._normalize({"type": "score", "score": 1.7, "confidence": 0.55,
                         "legend": {"0": "a"}, "probabilities": {"0": 0.1}})
    assert sc["value"] == 1.7 and "legend" in sc
    no = clf._normalize({"type": "noul", "noul": 0.98})
    assert no["value"] is True and no["probability"] == 0.98
    assert no["confidence"] == pytest.approx(0.96, abs=1e-6)
    no2 = clf._normalize({"type": "noul", "noul": 0.1})
    assert no2["value"] is False


# --- jev_query (mock de requests) ------------------------------------------

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)
    def json(self):
        return self._payload


def test_jev_query_ok(monkeypatch):
    import requests
    captura = {}
    def fake_post(url, json=None, timeout=None, headers=None):
        captura["url"] = url; captura["body"] = json; captura["headers"] = headers
        return _Resp(200, {"model": "jev-latest",
                           "answers": {"mode": {"type": "choice", "choice": "directo",
                                                "confidence": 0.9, "probabilities": {"directo": 0.9, "orquestado": 0.1}}},
                           "usage": {"input_tokens": 10}})
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k-test")
    out = clf.jev_query("hola", {"mode": clf.choice("m?", {"directo": "x", "orquestado": "y"})})
    assert out["mode"]["value"] == "directo" and out["mode"]["confidence"] == 0.9
    assert captura["headers"]["Authorization"] == "Bearer k-test"
    assert captura["body"]["model"] == "jev-latest"


def test_jev_query_sin_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(clf.ClassifierError):
        clf.jev_query("x", {"q": clf.boolean("¿?")})


def test_jev_query_error_http(monkeypatch):
    import requests
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(422, {"detail": "Field required"}))
    with pytest.raises(clf.ClassifierError):
        clf.jev_query("x", {"q": clf.boolean("¿?")})


# --- fallback LLM -----------------------------------------------------------

class _FakeProv:
    def __init__(self, texto): self._t = texto
    def completar(self, mensajes, temperature=0, **k):
        class R: pass
        r = R(); r.texto = self._t; return r


def test_llm_fallback_parsea_json():
    prov = _FakeProv('Acá va: {"mode": {"value": "orquestado", "confidence": 0.8}, '
                     '"needs_scope": {"value": true, "confidence": 0.9}}')
    c = clf.LLMClassifier(provider=prov)
    out = c.classify("tarea", {"mode": clf.choice("m?", {"directo": "x", "orquestado": "y"}),
                               "needs_scope": clf.boolean("¿activa?")})
    assert out["mode"]["value"] == "orquestado"
    assert out["needs_scope"]["value"] is True


# --- make_classifier --------------------------------------------------------

def test_make_classifier_elige_jev_si_hay_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert clf.make_classifier().name == "jev"
    assert clf.make_classifier(prefer_local=True).name == "llm"


def test_make_classifier_cae_a_llm_sin_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert clf.make_classifier().name == "llm"


# --- classify_task (ruteo) --------------------------------------------------

class _ClfFijo:
    name = "fake"
    def __init__(self, ans): self._ans = ans
    def classify(self, state, questions): return self._ans


def test_classify_task_confianza_baja_orquesta():
    # mode=directo pero con confianza 0.4 (< 0.6) -> debe caer a orquestado.
    ans = {"mode": {"type": "choice", "value": "directo", "confidence": 0.4},
           "complexity": {"type": "score", "value": 0.0},
           "category": {"type": "choice", "value": "informe"},
           "needs_scope": {"type": "noul", "value": False, "probability": 0.1}}
    r = clf.classify_task("x", classifier=_ClfFijo(ans))
    assert r["mode"] == "orquestado" and r["confianza_baja"] is True


def test_classify_task_directo_con_confianza():
    ans = {"mode": {"type": "choice", "value": "directo", "confidence": 0.95},
           "complexity": {"type": "score", "value": 0.0},
           "category": {"type": "choice", "value": "informe"},
           "needs_scope": {"type": "noul", "value": False, "probability": 0.05}}
    r = clf.classify_task("x", classifier=_ClfFijo(ans))
    assert r["mode"] == "directo" and r["needs_scope"] is False


def test_classify_task_error_defaultea_orquestado():
    class _Boom:
        name = "boom"
        def classify(self, *a): raise RuntimeError("falló")
    r = clf.classify_task("x", classifier=_Boom())
    assert r["mode"] == "orquestado" and r.get("error")
