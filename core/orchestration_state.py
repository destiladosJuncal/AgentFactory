"""
Orchestration state — the backbone the orchestrator hangs everything off.

One run = one `OrchestrationState`: the task, how the router classified it, which
model orchestrates it, and the plan (sub-tasks with their objective, deliverable,
dependencies, status, result and a small history). It persists to disk after
every change so the run can be:

  - resumed after a stop/crash (checkpointing),
  - supervised/steered (each sub-task keeps a history of course-corrections),
  - shown live as an editable, collapsible plan with "objectives done".

Layout: <datos>/_orquestaciones/<run_id>/estado.json
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Run status.
PLANNING = "planning"            # Fase A: planeando/conversando, nada ejecutado
AWAITING_APPROVAL = "awaiting"   # plan listo, esperando tu OK (o el gate de scope)
RUNNING = "running"              # Fase B: workers en marcha
PAUSED = "paused"                # detenido, se puede reanudar
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

# Sub-task status.
PENDING = "pending"
IN_PROGRESS = "running"
DONE_T = "done"
ERROR_T = "error"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class SubTask:
    """A unit of work in the plan. `deliverable` is the expected output contract
    (delegación de alta calidad); `history` logs course-corrections (steering)."""
    id: str
    objective: str
    deliverable: str = ""
    depends_on: List[str] = field(default_factory=list)
    status: str = PENDING
    result: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SubTask":
        return cls(
            id=str(d.get("id") or ""),
            objective=str(d.get("objective") or ""),
            deliverable=str(d.get("deliverable") or ""),
            depends_on=list(d.get("depends_on") or []),
            status=str(d.get("status") or PENDING),
            result=str(d.get("result") or ""),
            history=list(d.get("history") or []),
        )

    def log(self, kind: str, detail: str = ""):
        """Record a steering/lifecycle event on this sub-task."""
        self.history.append({"ts": _now(), "kind": kind, "detail": detail})


def runs_dir(base_dir: Optional[Path] = None) -> Path:
    if base_dir is not None:
        d = Path(base_dir)
    else:
        from core.rutas import dir_datos
        d = dir_datos() / "_orquestaciones"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class OrchestrationState:
    run_id: str
    task: str
    created: str = ""
    updated: str = ""
    status: str = PLANNING
    classification: Dict[str, Any] = field(default_factory=dict)
    orchestrator_model: str = ""     # 'claude' | 'deepseek' | model id
    scope_confirmed: bool = False    # gate de autorización (categorías activas)
    subtasks: List[SubTask] = field(default_factory=list)
    synthesis: str = ""
    error: str = ""

    # -- construction --------------------------------------------------------

    @classmethod
    def create(cls, task: str, classification: Optional[Dict[str, Any]] = None,
               orchestrator_model: str = "", base_dir: Optional[Path] = None) -> "OrchestrationState":
        rid = "orq-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        st = cls(run_id=rid, task=(task or "").strip(), created=_now(),
                 classification=dict(classification or {}),
                 orchestrator_model=orchestrator_model)
        st.save(base_dir)
        return st

    # -- serialization -------------------------------------------------------

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["subtasks"] = [s.as_dict() for s in self.subtasks]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OrchestrationState":
        st = cls(
            run_id=str(d.get("run_id") or ""),
            task=str(d.get("task") or ""),
            created=str(d.get("created") or ""),
            updated=str(d.get("updated") or ""),
            status=str(d.get("status") or PLANNING),
            classification=dict(d.get("classification") or {}),
            orchestrator_model=str(d.get("orchestrator_model") or ""),
            scope_confirmed=bool(d.get("scope_confirmed")),
            synthesis=str(d.get("synthesis") or ""),
            error=str(d.get("error") or ""),
        )
        st.subtasks = [SubTask.from_dict(s) for s in (d.get("subtasks") or [])]
        return st

    # -- persistence ---------------------------------------------------------

    def path(self, base_dir: Optional[Path] = None) -> Path:
        return runs_dir(base_dir) / self.run_id / "estado.json"

    def save(self, base_dir: Optional[Path] = None) -> Path:
        """Escribe el estado de forma atómica (tmp + replace), tras tocar `updated`."""
        self.updated = _now()
        p = self.path(base_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.as_dict(), indent=2, ensure_ascii=False, default=str),
                       encoding="utf-8")
        tmp.replace(p)
        return p

    @classmethod
    def load(cls, run_id: str, base_dir: Optional[Path] = None) -> Optional["OrchestrationState"]:
        p = runs_dir(base_dir) / run_id / "estado.json"
        if not p.exists():
            return None
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    # -- plan helpers --------------------------------------------------------

    def subtask(self, sid: str) -> Optional[SubTask]:
        for s in self.subtasks:
            if s.id == sid:
                return s
        return None

    def set_plan(self, subtasks: List[SubTask]):
        self.subtasks = list(subtasks)

    def add_subtasks(self, subtasks: List[SubTask]):
        existing = {s.id for s in self.subtasks}
        for s in subtasks:
            if s.id not in existing:
                self.subtasks.append(s)
                existing.add(s.id)

    def progress(self) -> Dict[str, int]:
        done = sum(1 for s in self.subtasks if s.status == DONE_T)
        err = sum(1 for s in self.subtasks if s.status == ERROR_T)
        return {"done": done, "error": err, "total": len(self.subtasks)}

    def is_complete(self) -> bool:
        return bool(self.subtasks) and all(
            s.status in (DONE_T, ERROR_T) for s in self.subtasks)


def list_runs(base_dir: Optional[Path] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Resumen de las corridas guardadas, más recientes primero. Para el panel y
    para ofrecer 'Reanudar' las que quedaron a medias."""
    out: List[Dict[str, Any]] = []
    for carpeta in runs_dir(base_dir).iterdir():
        if not carpeta.is_dir():
            continue
        p = carpeta / "estado.json"
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        subs = d.get("subtasks") or []
        done = sum(1 for s in subs if s.get("status") == DONE_T)
        out.append({
            "run_id": d.get("run_id") or carpeta.name,
            "task": (d.get("task") or "")[:120],
            "status": d.get("status") or "",
            "created": d.get("created") or "",
            "updated": d.get("updated") or "",
            "done": done, "total": len(subs),
            "resumable": d.get("status") in (RUNNING, PAUSED, AWAITING_APPROVAL),
        })
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out[:limit]
