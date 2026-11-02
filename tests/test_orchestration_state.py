"""Tests del backbone de estado de orquestación (core.orchestration_state).

Valida creación/persistencia atómica, round-trip de serialización (incluido el
historial de steering), helpers del plan (dedup, progreso, completitud) y el
listado de corridas para 'Reanudar'. Usa tmp_path, no toca los datos reales.
"""

from core import orchestration_state as st
from core.orchestration_state import OrchestrationState, SubTask


def test_create_persiste_y_recarga(tmp_path):
    s = OrchestrationState.create("hacé A y B", classification={"mode": "orquestado"},
                                  orchestrator_model="deepseek", base_dir=tmp_path)
    assert s.run_id.startswith("orq-")
    assert s.path(tmp_path).exists()
    cargado = OrchestrationState.load(s.run_id, base_dir=tmp_path)
    assert cargado is not None
    assert cargado.task == "hacé A y B"
    assert cargado.classification["mode"] == "orquestado"
    assert cargado.orchestrator_model == "deepseek"
    assert cargado.status == st.PLANNING


def test_subtask_roundtrip_con_historial(tmp_path):
    s = OrchestrationState.create("tarea", base_dir=tmp_path)
    t = SubTask(id="a", objective="hacer A", deliverable="un archivo .md",
                depends_on=["x"])
    t.log("correccion", "reorientado por el orquestador")
    t.status = st.DONE_T
    t.result = "listo"
    s.set_plan([t])
    s.save(tmp_path)

    r = OrchestrationState.load(s.run_id, base_dir=tmp_path)
    ta = r.subtask("a")
    assert ta is not None
    assert ta.deliverable == "un archivo .md" and ta.depends_on == ["x"]
    assert ta.status == st.DONE_T and ta.result == "listo"
    assert len(ta.history) == 1 and ta.history[0]["kind"] == "correccion"


def test_add_subtasks_dedup():
    s = OrchestrationState(run_id="r", task="t")
    s.set_plan([SubTask(id="a", objective="A")])
    s.add_subtasks([SubTask(id="a", objective="A dup"), SubTask(id="b", objective="B")])
    assert [x.id for x in s.subtasks] == ["a", "b"]   # 'a' no se duplicó


def test_progress_y_completitud():
    s = OrchestrationState(run_id="r", task="t")
    s.set_plan([SubTask(id="a", objective="A", status=st.DONE_T),
                SubTask(id="b", objective="B", status=st.PENDING),
                SubTask(id="c", objective="C", status=st.ERROR_T)])
    p = s.progress()
    assert p == {"done": 1, "error": 1, "total": 3}
    assert s.is_complete() is False
    s.subtask("b").status = st.DONE_T
    assert s.is_complete() is True   # todas done/error


def test_list_runs_marca_resumable(tmp_path):
    a = OrchestrationState.create("vieja", base_dir=tmp_path)
    a.status = st.DONE
    a.save(tmp_path)
    b = OrchestrationState.create("a medias", base_dir=tmp_path)
    b.status = st.RUNNING
    b.save(tmp_path)

    runs = st.list_runs(base_dir=tmp_path)
    assert {r["run_id"] for r in runs} == {a.run_id, b.run_id}
    resumables = {r["run_id"]: r["resumable"] for r in runs}
    assert resumables[b.run_id] is True    # RUNNING -> se puede reanudar
    assert resumables[a.run_id] is False   # DONE -> no


def test_list_runs_ordena_por_updated(tmp_path):
    # Orden determinista: escribo dos estados con 'updated' distintos a mano.
    for rid, upd in [("orq-vieja", "2020-01-01 00:00:00"),
                     ("orq-nueva", "2030-01-01 00:00:00")]:
        s = OrchestrationState(run_id=rid, task=rid)
        s.save(tmp_path)
        p = s.path(tmp_path)
        import json
        d = json.loads(p.read_text()); d["updated"] = upd
        p.write_text(json.dumps(d))
    runs = st.list_runs(base_dir=tmp_path)
    assert runs[0]["run_id"] == "orq-nueva"   # más reciente primero
    assert runs[-1]["run_id"] == "orq-vieja"


def test_load_inexistente_devuelve_none(tmp_path):
    assert OrchestrationState.load("orq-no-existe", base_dir=tmp_path) is None
