"""Tests del motor orquestador–worker (core.orchestrator.Orchestrator).

Opus (orquestador) planifica/revisa/sintetiza; los workers (DeepSeek) resuelven
en paralelo. Acá ambos seams son fakes, así el motor corre sin API: validamos la
descomposición, el paralelismo con dependencias, la iteración (re-plan), el tope
de rondas, la cancelación y los fallbacks.
"""

import json
import threading
from core.orchestrator import Orchestrator, SubTask


class FakeOrq:
    """LLM del orquestador scripteado: responde según la FASE del prompt."""
    def __init__(self, plan, reviews, synthesis="SÍNTESIS FINAL"):
        self.plan = plan                 # lista de dicts para el array del plan
        self.reviews = list(reviews)     # un dict por revisión (en orden)
        self.synthesis = synthesis
        self.calls = {"plan": 0, "review": 0, "synth": 0}

    def __call__(self, prompt: str) -> str:
        if "Break the task" in prompt:          # _PLAN_PROMPT
            self.calls["plan"] += 1
            return "acá va el plan: " + json.dumps(self.plan)
        if "reviewing your workers" in prompt:   # _REVIEW_PROMPT
            self.calls["review"] += 1
            v = self.reviews.pop(0) if self.reviews else {"done": True, "synthesis": "", "new_subtasks": []}
            return json.dumps(v)
        if "Write the final answer" in prompt:   # _SYNTH_PROMPT
            self.calls["synth"] += 1
            return self.synthesis
        return "{}"


def hacer_worker(registro, resultados=None):
    lock = threading.Lock()
    def run(sub: SubTask, context):
        with lock:
            registro.append({"id": sub.id, "context": dict(context)})
        return (resultados or {}).get(sub.id, f"hecho:{sub.id}")
    return run


def test_una_pasada_plan_workers_sintesis():
    orq = FakeOrq(plan=[{"id": "a", "objective": "A"}, {"id": "b", "objective": "B"}],
                  reviews=[{"done": True, "synthesis": "RESPUESTA"}])
    reg = []
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker(reg))
    out = o.run("tarea")
    assert out == "RESPUESTA"
    assert {r["id"] for r in reg} == {"a", "b"}   # corrieron los dos workers
    assert orq.calls["plan"] == 1 and orq.calls["review"] == 1


def test_dependencias_pasan_contexto_y_ordenan():
    # b depende de a: a corre primero y su resultado llega como contexto de b.
    orq = FakeOrq(plan=[{"id": "a", "objective": "A"},
                        {"id": "b", "objective": "B", "depends_on": ["a"]}],
                  reviews=[{"done": True, "synthesis": "OK"}])
    reg = []
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker(reg, {"a": "RES_A"}))
    o.run("tarea")
    por_id = {r["id"]: r for r in reg}
    assert "a" in por_id and "b" in por_id
    assert por_id["b"]["context"] == {"a": "RES_A"}   # b recibió el resultado de a


def test_iterativo_agrega_subtareas():
    # 1ra revisión: no terminado + una sub-tarea nueva. 2da: terminado.
    orq = FakeOrq(plan=[{"id": "a", "objective": "A"}],
                  reviews=[{"done": False, "new_subtasks": [{"id": "c", "objective": "C"}]},
                           {"done": True, "synthesis": "CERRADO"}])
    reg = []
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker(reg))
    out = o.run("tarea")
    assert out == "CERRADO"
    assert {r["id"] for r in reg} == {"a", "c"}   # la nueva sub-tarea se ejecutó
    assert orq.calls["review"] == 2


def test_tope_de_rondas_sintetiza():
    # Siempre "no terminado" y sin nuevas: no debe girar infinito.
    orq = FakeOrq(plan=[{"id": "a", "objective": "A"}],
                  reviews=[{"done": False, "new_subtasks": []}] * 10)
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker([]), max_rounds=3)
    out = o.run("tarea")
    assert out == "SÍNTESIS FINAL"          # cayó en la síntesis final
    assert orq.calls["synth"] == 1


def test_cancelacion_corta():
    orq = FakeOrq(plan=[{"id": "a", "objective": "A"}], reviews=[{"done": False}])
    corrio = []
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker(corrio),
                     should_stop=lambda: True)   # cancelado de entrada
    out = o.run("tarea")
    assert out == "SÍNTESIS FINAL"   # sintetiza con lo que haya (nada)
    assert corrio == []              # ningún worker llegó a correr


def test_plan_invalido_cae_a_un_worker():
    orq = FakeOrq(plan="esto no es una lista", reviews=[{"done": True, "synthesis": "Z"}])
    reg = []
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker(reg))
    out = o.run("resolver todo")
    assert out == "Z"
    assert len(reg) == 1             # un solo worker con la tarea completa


def test_progreso_emite_eventos():
    eventos = []
    orq = FakeOrq(plan=[{"id": "a", "objective": "A"}],
                  reviews=[{"done": True, "synthesis": "fin"}])
    o = Orchestrator(plan_llm=orq, run_worker=hacer_worker([]),
                     progress=lambda ev: eventos.append(ev["type"]))
    o.run("tarea")
    assert "start" in eventos and "plan" in eventos
    assert "worker_start" in eventos and "worker_done" in eventos
    assert "done" in eventos
