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


def test_streaming_de_razonamiento_del_gestor():
    # Un orquestador que streamea tokens vía on_token: deben salir eventos 'think'
    # con la fase correspondiente (plan / review / synth).
    class OrqStream:
        def __init__(self):
            self.rev = [{"done": False, "new_subtasks": []}]  # fuerza síntesis final
        def __call__(self, prompt, on_token=None):
            if "Break the task" in prompt:
                if on_token: on_token("pienso el plan…")
                return json.dumps([{"id": "a", "objective": "A"}])
            if "reviewing your workers" in prompt:
                if on_token: on_token("reviso…")
                return json.dumps(self.rev.pop(0) if self.rev else {"done": True})
            if "Write the final answer" in prompt:
                if on_token: on_token("sintetizo…")
                return "LISTO"
            return "{}"
    fases = []
    o = Orchestrator(plan_llm=OrqStream(), run_worker=hacer_worker([]), max_rounds=1,
                     progress=lambda ev: fases.append(ev.get("phase")) if ev["type"] == "think" else None)
    out = o.run("tarea")
    assert out == "LISTO"
    assert "plan" in fases and "review" in fases and "synth" in fases


def test_fraccionado_enriquece_el_plan():
    # Con fractioner que devuelve 2 partes: se emite 'fraction' y el prompt del
    # plan incluye esas partes (Opus recibe algo prolijo).
    prompts = []
    class OrqSpy:
        def __call__(self, prompt, on_token=None):
            prompts.append(prompt)
            if "Break the task" in prompt:
                return json.dumps([{"id": "a", "objective": "A"}])
            if "reviewing your workers" in prompt:
                return json.dumps({"done": True, "synthesis": "OK"})
            return "{}"
    eventos = []
    o = Orchestrator(plan_llm=OrqSpy(), run_worker=hacer_worker([]),
                     fractioner=lambda t: ["parte uno", "parte dos"],
                     progress=lambda ev: eventos.append(ev))
    o.run("hacé A y también B")
    frac = [e for e in eventos if e["type"] == "fraction"]
    assert frac and frac[0]["parts"] == ["parte uno", "parte dos"]
    plan_prompt = next(p for p in prompts if "Break the task" in p)
    assert "parte uno" in plan_prompt and "parte dos" in plan_prompt


def test_fraccionado_una_sola_parte_no_fracciona():
    eventos = []
    o = Orchestrator(plan_llm=FakeOrq(plan=[{"id": "a", "objective": "A"}],
                                      reviews=[{"done": True, "synthesis": "OK"}]),
                     run_worker=hacer_worker([]),
                     fractioner=lambda t: ["una sola cosa"],
                     progress=lambda ev: eventos.append(ev))
    o.run("tarea simple")
    assert not [e for e in eventos if e["type"] == "fraction"]


def test_run_con_plan_aprobado_no_replanifica_y_muta_estado():
    # Fase B: se le pasa el plan aprobado; NO debe planificar, y debe ejecutar
    # mutando los SubTask in place (status/result) para que el estado persista.
    plan_llamado = []
    class OrqSpy:
        def __call__(self, prompt, on_token=None):
            if "Break the task" in prompt:
                plan_llamado.append(1)            # no debería pasar
                return "[]"
            if "reviewing your workers" in prompt:
                return json.dumps({"done": True, "synthesis": "OK"})
            return "OK"
    aprobado = [SubTask(id="a", objective="A"), SubTask(id="b", objective="B", depends_on=["a"])]
    o = Orchestrator(plan_llm=OrqSpy(),
                     run_worker=lambda s, ctx: f"resultado-{s.id}")
    out = o.run("tarea", subtasks=aprobado)
    assert out == "OK"
    assert plan_llamado == []                      # no re-planificó
    assert aprobado[0].status == "done" and aprobado[0].result == "resultado-a"
    assert aprobado[1].result == "resultado-b"     # respetó la dependencia y corrió


def test_run_saltea_subtareas_done():
    # Resume: una sub-tarea ya 'done' no se re-corre y conserva su resultado.
    corridos = []
    done_sub = SubTask(id="a", objective="A", status="done", result="ya estaba")
    pend = SubTask(id="b", objective="B")
    o = Orchestrator(plan_llm=FakeOrq(plan=[], reviews=[{"done": True, "synthesis": "OK"}]),
                     run_worker=lambda s, ctx: (corridos.append(s.id) or f"r-{s.id}"))
    o.run("t", subtasks=[done_sub, pend])
    assert corridos == ["b"]              # 'a' se salteó
    assert done_sub.result == "ya estaba" and pend.result == "r-b"


def test_steering_redirect_luego_ok(monkeypatch):
    # El worker corre; el supervisor dice 'redirect' con feedback -> el worker
    # rehace (misma conversación) -> 'ok'. Debe quedar registrado en history.
    import core.orchestrator as orqmod
    from core.orchestration_state import SubTask

    turnos = []
    class FakeChat:
        cancelado = False
        def enviar(self, prompt, al_fragmento=None, al_evento=None):
            turnos.append(prompt)
            return f"intento-{len(turnos)}"
    class FakeGestor:
        def __init__(self, *a): pass
        def crear_conversacion(self, nombre): return "/tmp/x"
    monkeypatch.setattr(orqmod, "GestorConversaciones", FakeGestor, raising=False)
    # parchear imports locales dentro de run()
    import core.conversaciones, core.chat, core.proveedores
    monkeypatch.setattr(core.conversaciones, "GestorConversaciones", FakeGestor)
    monkeypatch.setattr(core.chat, "ConversacionChat", lambda d: FakeChat())
    monkeypatch.setattr(core.proveedores, "crear_proveedor", lambda *a, **k: None)

    veredictos = iter([
        '{"verdict":"redirect","feedback":"faltó el punto B"}',
        '{"verdict":"ok"}',
    ])
    supervisor = lambda messages, on_token=None: next(veredictos)
    eventos = []
    runner = orqmod.deepseek_worker_runner(
        tools=False, supervisor=supervisor, max_corrections=2,
        on_event=lambda wid, kind, data: eventos.append((kind, data)))
    sub = SubTask(id="a", objective="hacer A y B", deliverable="informe")
    res = runner(sub, {})
    assert res == "intento-2"                 # rehízo una vez tras la corrección
    assert len(turnos) == 2
    assert any(k == "steer" for k, _ in eventos)
    assert sub.history and sub.history[0]["kind"] == "redirect"


def test_steering_respeta_tope(monkeypatch):
    import core.orchestrator as orqmod
    from core.orchestration_state import SubTask
    import core.conversaciones, core.chat, core.proveedores
    turnos = []
    class FakeChat:
        cancelado = False
        def enviar(self, prompt, al_fragmento=None, al_evento=None):
            turnos.append(1); return "x"
    class FakeGestor:
        def __init__(self, *a): pass
        def crear_conversacion(self, n): return "/tmp/x"
    monkeypatch.setattr(core.conversaciones, "GestorConversaciones", FakeGestor)
    monkeypatch.setattr(core.chat, "ConversacionChat", lambda d: FakeChat())
    monkeypatch.setattr(core.proveedores, "crear_proveedor", lambda *a, **k: None)
    supervisor = lambda messages, on_token=None: '{"verdict":"redirect","feedback":"más"}'
    runner = orqmod.deepseek_worker_runner(tools=False, supervisor=supervisor, max_corrections=2)
    sub = SubTask(id="a", objective="o")
    runner(sub, {})
    # 1 turno inicial + 2 correcciones (tope) = 3
    assert len(turnos) == 3
    assert len(sub.history) == 2


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
