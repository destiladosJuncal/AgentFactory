"""
Orchestrator–worker swarm: a capable, pricey model (Opus) MANAGES the work, and
several cheap workers (DeepSeek) do the heavy lifting in parallel.

Flow (iterative, with a round cap):

  1. The orchestrator PLANS: it breaks the task into sub-tasks (with
     dependencies between them).
  2. Sub-tasks whose dependencies are already satisfied run IN PARALLEL; each
     worker is a DeepSeek agent WITH tools (it can run shell, read/write files,
     use the proxy…, and the confirmation guard stays in force).
  3. The orchestrator REVIEWS the results and decides: are we done, or are more
     sub-tasks needed?
  4. Repeat until it closes or hits the round cap.
  5. The orchestrator SYNTHESIZES the final answer from everything gathered.

Why this shape: the orchestrator only thinks and coordinates (few calls, few
tokens), while the bulk runs on cheap workers in parallel — the cost-efficient
way to use a premium model.

The ``plan_llm`` and ``run_worker`` seams are injected in tests so the engine
runs without any API.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

MAX_ROUNDS = 3
MAX_SUBTASKS = 8
MAX_PARALLEL = 4

# LLM seam for the orchestrator: (prompt, on_token) -> text reply. on_token, when
# given, receives the reply token-by-token so the UI can show Opus reasoning live.
OrchestratorLLM = Callable[..., str]
# Worker seam: (sub-task, context from its dependencies) -> result text.
WorkerRunner = Callable[["SubTask", Dict[str, str]], str]
# Progress seam: called with small dicts describing each step, for the UI.
Progress = Callable[[Dict[str, Any]], None]


@dataclass
class SubTask:
    id: str
    objective: str
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"      # pending | running | done | error
    result: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "objective": self.objective,
                "depends_on": list(self.depends_on),
                "status": self.status, "result": self.result}


_PLAN_PROMPT = (
    "You are an orchestrator managing a team of worker agents. Break the task "
    "below into the SMALLEST set of concrete, independent-where-possible "
    "sub-tasks that solve it well. Each worker is capable and can run tools "
    "(shell, files, web proxy). Use between 1 and {max} sub-tasks: only as many "
    "as genuinely help.\n\n"
    "First write ONE short sentence with your plan of attack, then output the "
    "JSON array (and nothing after it). Each item:\n"
    '  {{"id": "<short-slug>", "objective": "<what this worker must do, '
    'self-contained>", "depends_on": ["<id>", ...]}}\n'
    "Use depends_on only when a sub-task truly needs another's output first.\n\n"
    "Task:\n{task}"
)

_REVIEW_PROMPT = (
    "You are an orchestrator reviewing your workers' results for this task:\n"
    "{task}\n\n"
    "Sub-tasks and their results so far:\n{results}\n\n"
    "Decide whether the task is fully solved. First write ONE short sentence "
    "with your assessment, then output the JSON object (and nothing after it):\n"
    '  {{"done": <true|false>, '
    '"synthesis": "<if done: the complete final answer for the user>", '
    '"new_subtasks": [{{"id": "...", "objective": "...", "depends_on": []}}]}}\n'
    "Add new_subtasks ONLY if something is missing or wrong; otherwise leave it "
    "empty and set done=true with a synthesis."
)

_SYNTH_PROMPT = (
    "You are an orchestrator. Write the final answer for the user to this task:\n"
    "{task}\n\n"
    "Use everything your workers gathered:\n{results}\n\n"
    "Answer directly and completely. Do not mention the internal sub-task "
    "machinery unless it helps the user."
)


def _extract_json(text: str, opener: str, closer: str):
    """Pull the first JSON array/object out of a reply, tolerating stray prose
    or code fences. Returns the parsed value or None."""
    if not text:
        return None
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


class Orchestrator:
    def __init__(self, plan_llm: OrchestratorLLM, run_worker: WorkerRunner,
                 max_rounds: int = MAX_ROUNDS, max_subtasks: int = MAX_SUBTASKS,
                 max_parallel: int = MAX_PARALLEL,
                 progress: Optional[Progress] = None,
                 should_stop: Optional[Callable[[], bool]] = None):
        self.plan_llm = plan_llm
        self.run_worker = run_worker
        self.max_rounds = max(1, int(max_rounds))
        self.max_subtasks = max(1, int(max_subtasks))
        self.max_parallel = max(1, int(max_parallel))
        self._progress = progress
        self._should_stop = should_stop or (lambda: False)

    # -- seams wrappers ------------------------------------------------------

    def _emit(self, **ev):
        if self._progress:
            try:
                self._progress(ev)
            except Exception:
                pass

    def _stopped(self) -> bool:
        try:
            return bool(self._should_stop())
        except Exception:
            return False

    def _ask(self, prompt: str, phase: Optional[str]) -> str:
        """Call the orchestrator LLM. When ``phase`` is set, stream its tokens as
        ``think`` events so the UI shows Opus reasoning live."""
        on_token = None
        if phase:
            def on_token(t):  # noqa: E731 - small local sink
                self._emit(type="think", phase=phase, text=t)
        try:
            return self.plan_llm(prompt, on_token)
        except TypeError:
            # A seam that only takes the prompt (e.g. a simple test fake).
            return self.plan_llm(prompt)

    # -- phases --------------------------------------------------------------

    def _plan(self, task: str) -> List[SubTask]:
        reply = self._ask(_PLAN_PROMPT.format(max=self.max_subtasks, task=task), "plan")
        raw = _extract_json(reply, "[", "]")
        subtasks: List[SubTask] = []
        seen = set()
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                objective = str(item.get("objective") or "").strip()
                if not objective:
                    continue
                sid = str(item.get("id") or f"t{len(subtasks) + 1}").strip() or f"t{len(subtasks) + 1}"
                while sid in seen:
                    sid += "_"
                seen.add(sid)
                deps = [str(d).strip() for d in (item.get("depends_on") or []) if str(d).strip()]
                subtasks.append(SubTask(id=sid, objective=objective, depends_on=deps))
                if len(subtasks) >= self.max_subtasks:
                    break
        if not subtasks:
            # Fallback: a single worker solves the whole thing.
            subtasks = [SubTask(id="t1", objective=task)]
        # Drop dependencies that point at unknown ids (keeps scheduling sane).
        ids = {s.id for s in subtasks}
        for s in subtasks:
            s.depends_on = [d for d in s.depends_on if d in ids and d != s.id]
        return subtasks

    def _run_round(self, ready: List[SubTask], done: Dict[str, str]):
        """Run the ready sub-tasks in parallel; fill their result/status."""
        def worker(s: SubTask):
            if self._stopped():
                s.status = "error"; s.result = "(cancelled)"
                return
            s.status = "running"
            self._emit(type="worker_start", id=s.id, objective=s.objective)
            try:
                context = {d: done[d] for d in s.depends_on if d in done}
                s.result = self.run_worker(s, context) or ""
                s.status = "done"
            except Exception as e:  # a worker blowing up must not kill the swarm
                s.result = f"(error: {e})"
                s.status = "error"
            self._emit(type="worker_done", id=s.id, status=s.status,
                       result=s.result[:300])

        workers = min(self.max_parallel, len(ready)) or 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, ready))

    def _results_block(self, subtasks: List[SubTask]) -> str:
        lines = []
        for s in subtasks:
            lines.append(f"[{s.id}] ({s.status}) {s.objective}\n→ {s.result or '(no result)'}")
        return "\n\n".join(lines)

    def _review(self, task: str, subtasks: List[SubTask]) -> Dict[str, Any]:
        reply = self._ask(_REVIEW_PROMPT.format(
            task=task, results=self._results_block(subtasks)), "review")
        obj = _extract_json(reply, "{", "}")
        if not isinstance(obj, dict):
            # Can't parse a verdict: treat as "not done" so we either add nothing
            # and fall through to a synthesis, or stop at the round cap.
            return {"done": False, "synthesis": "", "new_subtasks": []}
        return obj

    def _synthesize(self, task: str, subtasks: List[SubTask]) -> str:
        reply = self._ask(_SYNTH_PROMPT.format(
            task=task, results=self._results_block(subtasks)), "synth")
        return (reply or "").strip()

    # -- main loop -----------------------------------------------------------

    def _drain(self, subtasks: List[SubTask], done: Dict[str, str]):
        """Run the pending sub-tasks in parallel WAVES, respecting dependencies,
        until nothing more is runnable (the DAG is drained for this round)."""
        wave = 0
        while not self._stopped():
            ready = [s for s in subtasks
                     if s.status == "pending" and all(d in done for d in s.depends_on)]
            if not ready:
                break  # all done, or remaining tasks have unsatisfiable deps
            wave += 1
            self._emit(type="round", wave=wave, running=[s.id for s in ready])
            self._run_round(ready, done)
            for s in ready:
                done[s.id] = s.result

    def run(self, task: str) -> str:
        self._emit(type="start", task=task)
        subtasks = self._plan(task)
        self._emit(type="plan", subtasks=[s.as_dict() for s in subtasks])
        done: Dict[str, str] = {}

        for round_no in range(self.max_rounds):
            if self._stopped():
                break

            # Drain the whole DAG planned so far before asking the orchestrator.
            self._drain(subtasks, done)

            last_round = (round_no == self.max_rounds - 1)
            if self._stopped():
                break

            verdict = self._review(task, subtasks)
            if verdict.get("done"):
                synthesis = (verdict.get("synthesis") or "").strip() or self._synthesize(task, subtasks)
                self._emit(type="done", synthesis=synthesis)
                return synthesis

            # Not done: queue any new sub-tasks the orchestrator asked for.
            added = 0
            existing = {s.id for s in subtasks}
            for nd in (verdict.get("new_subtasks") or []):
                if not isinstance(nd, dict) or len(subtasks) >= self.max_subtasks:
                    continue
                objective = str(nd.get("objective") or "").strip()
                if not objective:
                    continue
                sid = str(nd.get("id") or f"t{len(subtasks) + 1}").strip()
                while sid in existing:
                    sid += "_"
                existing.add(sid)
                deps = [str(d).strip() for d in (nd.get("depends_on") or [])
                        if str(d).strip() in existing]
                subtasks.append(SubTask(id=sid, objective=objective, depends_on=deps))
                added += 1
            self._emit(type="replan", added=added)
            if not added and not last_round:
                # Nothing new and not done: avoid spinning — synthesize now.
                break

        synthesis = self._synthesize(task, subtasks)
        self._emit(type="done", synthesis=synthesis)
        return synthesis


# --- real seams (wired to the providers) ------------------------------------

def opus_planner(model: Optional[str] = None) -> OrchestratorLLM:
    """Orchestrator LLM backed by Claude (Opus by default). Tool-less: it only
    plans, reviews and synthesizes. Raises ErrorProveedor if there's no key."""
    from core.proveedores import crear_proveedor
    provider = crear_proveedor("claude")
    if model:
        provider.modelo = model

    def ask(prompt: str, on_token=None) -> str:
        resp = provider.completar(
            mensajes=[{"role": "user", "content": prompt}], temperature=0.2,
            al_fragmento=on_token)
        return resp.texto or ""
    return ask


def deepseek_worker_runner(tools: bool = True,
                           worker_provider: str = "deepseek",
                           registry: Optional[list] = None,
                           on_event: Optional[Callable[[str, str, Any], None]] = None) -> WorkerRunner:
    """Each sub-task runs as a DeepSeek agent (its own conversation, full tool
    loop and confirmation guard when ``tools`` is on). Returns the worker's final
    text. If ``registry`` is given, each worker's chat is appended to it so the
    caller can cancel in-flight workers. If ``on_event`` is given, it receives
    (sub_id, kind, data): kind "think" with each streamed token, and kind "tool"
    with each tool event — so the UI can show the worker's reasoning live."""
    from core.conversaciones import GestorConversaciones
    from core.chat import ConversacionChat
    from core.proveedores import crear_proveedor

    gestor = GestorConversaciones()

    def run(sub: SubTask, context: Dict[str, str]) -> str:
        conv_dir = gestor.crear_conversacion(f"worker · {sub.id}")
        chat = ConversacionChat(conv_dir)
        try:
            chat.proveedor = crear_proveedor(worker_provider)
        except Exception:
            pass  # fall back to whatever the chat defaults to
        if hasattr(chat, "tools_habilitadas"):
            chat.tools_habilitadas = bool(tools)
        if registry is not None:
            registry.append(chat)
        ctx = ""
        if context:
            ctx = "\n\nContext from earlier sub-tasks:\n" + "\n".join(
                f"- [{k}] {v[:1500]}" for k, v in context.items())
        prompt = (f"You are a worker agent. Do exactly this and report the "
                  f"result concretely:\n\n{sub.objective}{ctx}")
        al_fragmento = (lambda t: on_event(sub.id, "think", t)) if on_event else None
        al_evento = (lambda ev: on_event(sub.id, "tool", ev)) if on_event else None
        return chat.enviar(prompt, al_fragmento=al_fragmento, al_evento=al_evento)
    return run
