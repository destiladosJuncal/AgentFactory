"""
Fase A — planificación conversacional (antes de delegar nada).

El orquestador (Opus por defecto; DeepSeek para categorías ofensivas) propone un
PLAN BORRADOR con sub-tareas de alta calidad —objetivo autocontenido, límites y
exclusiones explícitas, y un `deliverable` concreto— o, si el pedido es ambiguo,
hace hasta 3 preguntas. La persona charla/edita y recién después aprueba. Nada
se ejecuta en esta fase: es barato (solo texto) y afina mucho el trabajo.

Fallback: si la categoría ruteó a Claude y Claude BLOQUEA (motivo_fin=refusal),
reintenta con DeepSeek automáticamente (como sugiere la propia Usage Policy).
"""

import json
from typing import Any, Callable, Dict, List, Optional

from core.orchestration_state import SubTask

MAX_SUBTASKS = 8
MAX_QUESTIONS = 3

# Categorías activas/ofensivas: Claude las bloquea por policy aunque estén
# autorizadas, así que las orquesta DeepSeek de punta a punta.
OFFENSIVE_CATEGORIES = {"exploit_poc", "escaneo", "reversing_malware"}


class PlanningError(RuntimeError):
    pass


def orchestrator_provider_for(category: Optional[str]) -> str:
    """Qué proveedor orquesta esta categoría (editable)."""
    return "deepseek" if (category or "") in OFFENSIVE_CATEGORIES else "claude"


# Callable del orquestador: (messages, on_token) -> texto.
OrchestratorLLM = Callable[..., str]


def make_orchestrator_llm(category: Optional[str] = None,
                          on_fallback: Optional[Callable[[], None]] = None) -> OrchestratorLLM:
    """LLM del orquestador según categoría, con fallback Opus->DeepSeek si Claude
    bloquea o falla."""
    from core.proveedores import crear_proveedor, ErrorProveedor
    primary = orchestrator_provider_for(category)

    def ask(messages: List[Dict[str, str]], on_token=None) -> str:
        p = crear_proveedor(primary)
        if p is None:
            # Sin credenciales del primario: probá DeepSeek igual.
            p = crear_proveedor("deepseek")
            if p is None:
                raise PlanningError("No hay proveedor disponible para orquestar")
        try:
            r = p.completar(mensajes=messages, temperature=0.2, al_fragmento=on_token)
            bloqueo = getattr(r, "motivo_fin", None) == "refusal"
        except ErrorProveedor:
            r, bloqueo = None, True
        if primary == "claude" and bloqueo:
            if on_fallback:
                try:
                    on_fallback()
                except Exception:
                    pass
            p2 = crear_proveedor("deepseek")
            if p2 is None:
                raise PlanningError("Claude bloqueó y no hay DeepSeek para el fallback")
            r = p2.completar(mensajes=messages, temperature=0.2, al_fragmento=on_token)
        return (getattr(r, "texto", "") or "") if r is not None else ""
    return ask


_PLAN_SYSTEM = (
    "Sos el ORQUESTADOR. Tu trabajo en esta fase es SOLO planificar (no ejecutás "
    "nada todavía). Proponé el conjunto MÁS CHICO de sub-tareas que resuelvan bien "
    "el pedido, repartibles a workers. Para CADA sub-tarea escribí:\n"
    "- objective: autocontenido, con LÍMITES explícitos (hacé solo X) y "
    "EXCLUSIONES que referencien a otras por id (NO hagas Y, eso es de <id>).\n"
    "- deliverable: el entregable concreto que debe producir (formato/resultado).\n"
    "- depends_on: solo cuando de verdad necesita el resultado de otra antes.\n"
    "Usá entre 1 y {max} sub-tareas. Elegí la topología que corresponda "
    "(secuencial con depends_on, o en paralelo sin dependencias).\n\n"
    "Si el pedido es AMBIGUO o le falta información clave, NO inventes: en vez del "
    "plan, hacé hasta {maxq} preguntas concretas.\n\n"
    "Respondé SOLO un objeto JSON, sin texto alrededor:\n"
    '{{"questions": ["..."], "plan": [{{"id": "slug", "objective": "...", '
    '"deliverable": "...", "depends_on": []}}], "note": "una línea de contexto"}}\n'
    "Dejá `questions` vacío si proponés plan; dejá `plan` vacío si preguntás."
)


def _extract_json_object(text: str):
    if not text:
        return None
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except (ValueError, TypeError):
        return None


def _subtasks_from(raw_plan: List[Dict[str, Any]]) -> List[SubTask]:
    subs: List[SubTask] = []
    seen = set()
    for item in raw_plan:
        if not isinstance(item, dict):
            continue
        objective = str(item.get("objective") or "").strip()
        if not objective:
            continue
        sid = str(item.get("id") or f"t{len(subs) + 1}").strip() or f"t{len(subs) + 1}"
        while sid in seen:
            sid += "_"
        seen.add(sid)
        deps = [str(d).strip() for d in (item.get("depends_on") or []) if str(d).strip()]
        subs.append(SubTask(id=sid, objective=objective,
                            deliverable=str(item.get("deliverable") or "").strip(),
                            depends_on=deps))
        if len(subs) >= MAX_SUBTASKS:
            break
    # Dependencias a ids inexistentes -> fuera.
    ids = {s.id for s in subs}
    for s in subs:
        s.depends_on = [d for d in s.depends_on if d in ids and d != s.id]
    return subs


def plan_turn(history: List[Dict[str, str]], classification: Optional[Dict[str, Any]] = None,
              llm: Optional[OrchestratorLLM] = None, on_token=None,
              max_subtasks: int = MAX_SUBTASKS) -> Dict[str, Any]:
    """Una vuelta de planificación conversacional.

    `history` es la conversación hasta ahora: [{role:'user', content:tarea}, ...].
    Devuelve {kind: 'plan'|'questions', subtasks, questions, note, reply}.
    Ante una respuesta imparseable, cae a un plan de una sola sub-tarea con el
    pedido completo (nunca deja al usuario sin avanzar)."""
    classification = classification or {}
    llm = llm or make_orchestrator_llm(classification.get("category"))
    system = _PLAN_SYSTEM.format(max=max_subtasks, maxq=MAX_QUESTIONS)
    messages = [{"role": "system", "content": system}] + list(history)
    reply = llm(messages, on_token)

    obj = _extract_json_object(reply) or {}
    questions = [str(q).strip() for q in (obj.get("questions") or []) if str(q).strip()][:MAX_QUESTIONS]
    subtasks = _subtasks_from(obj.get("plan") or [])
    note = str(obj.get("note") or "").strip()

    if subtasks:
        return {"kind": "plan", "subtasks": subtasks, "questions": [], "note": note, "reply": reply}
    if questions:
        return {"kind": "questions", "subtasks": [], "questions": questions, "note": note, "reply": reply}
    # Nada parseable: plan mínimo de una sub-tarea con la primera consigna del usuario.
    tarea = next((m["content"] for m in history if m.get("role") == "user"), "resolver la tarea")
    return {"kind": "plan",
            "subtasks": [SubTask(id="t1", objective=tarea, deliverable="resultado de la tarea")],
            "questions": [], "note": note, "reply": reply}
