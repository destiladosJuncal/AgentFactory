"""Tests de la Fase A — planificación conversacional (core.planning).

Fakes como LLM, sin API. Valida: plan con deliverable, modo preguntas, modelo
por categoría (ofensiva->deepseek), fallback Opus->DeepSeek ante bloqueo, y el
fallback a una sub-tarea cuando la respuesta es imparseable.
"""

import json
import pytest

from core import planning
from core.orchestration_state import SubTask


def _hist(tarea): return [{"role": "user", "content": tarea}]


def test_plan_con_deliverable_y_deps():
    reply = json.dumps({"plan": [
        {"id": "a", "objective": "Hacer A", "deliverable": "archivo A.md", "depends_on": []},
        {"id": "b", "objective": "Hacer B con A", "deliverable": "tabla", "depends_on": ["a"]},
    ], "questions": [], "note": "voy en cadena"})
    r = planning.plan_turn(_hist("hacé A y después B"), {"category": "informe"},
                           llm=lambda m, on_token=None: reply)
    assert r["kind"] == "plan"
    assert [s.id for s in r["subtasks"]] == ["a", "b"]
    assert r["subtasks"][0].deliverable == "archivo A.md"
    assert r["subtasks"][1].depends_on == ["a"]


def test_modo_preguntas_si_ambiguo():
    reply = json.dumps({"plan": [], "questions": ["¿qué carpeta?", "¿todos los clientes?"]})
    r = planning.plan_turn(_hist("buscá los contratos"), {"category": "recon"},
                           llm=lambda m, on_token=None: reply)
    assert r["kind"] == "questions"
    assert r["questions"] == ["¿qué carpeta?", "¿todos los clientes?"]
    assert r["subtasks"] == []


def test_reply_imparseable_cae_a_una_subtarea():
    r = planning.plan_turn(_hist("hacé algo"), {}, llm=lambda m, on_token=None: "no es json")
    assert r["kind"] == "plan" and len(r["subtasks"]) == 1
    assert r["subtasks"][0].objective == "hacé algo"


def test_orchestrator_provider_por_categoria():
    assert planning.orchestrator_provider_for("exploit_poc") == "deepseek"
    assert planning.orchestrator_provider_for("escaneo") == "deepseek"
    assert planning.orchestrator_provider_for("informe") == "claude"
    assert planning.orchestrator_provider_for(None) == "claude"


class _Prov:
    def __init__(self, texto="", motivo=None): self.texto = texto; self.motivo = motivo
    def completar(self, mensajes, temperature=0.2, al_fragmento=None):
        class R: pass
        r = R(); r.texto = self.texto; r.motivo_fin = self.motivo
        return r


def test_fallback_opus_a_deepseek_si_bloquea(monkeypatch):
    from core import proveedores
    llamados = []
    def fake_crear(prov=None):
        llamados.append(prov)
        if prov == "claude":
            return _Prov(texto="⚠️ Claude declinó", motivo="refusal")
        return _Prov(texto='{"plan":[{"id":"a","objective":"A","deliverable":"x"}]}')
    monkeypatch.setattr(proveedores, "crear_proveedor", fake_crear)
    marca = []
    llm = planning.make_orchestrator_llm("exploit_poc", on_fallback=lambda: marca.append(1))
    # categoría ofensiva -> primario deepseek, no debería tocar claude
    out = llm([{"role": "user", "content": "x"}])
    assert "plan" in out and "claude" not in llamados

    # ahora forzamos categoría que va a claude y bloquea -> cae a deepseek
    llamados.clear()
    llm2 = planning.make_orchestrator_llm("informe", on_fallback=lambda: marca.append(1))
    out2 = llm2([{"role": "user", "content": "x"}])
    assert "plan" in out2
    assert llamados[0] == "claude" and "deepseek" in llamados
    assert marca   # se avisó el fallback


def test_plan_respeta_tope_de_subtareas():
    muchas = [{"id": f"t{i}", "objective": f"obj {i}", "deliverable": "d"} for i in range(20)]
    reply = json.dumps({"plan": muchas, "questions": []})
    r = planning.plan_turn(_hist("mucho"), {}, llm=lambda m, on_token=None: reply)
    assert len(r["subtasks"]) == planning.MAX_SUBTASKS
