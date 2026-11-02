"""
Jev (TypeSafe AI) connector — a typed classifier for the orchestrator's router.

Jev is NOT an LLM: it takes a `state` (the task text) plus one or more *typed*
questions and returns, per question, a calibrated answer with a confidence, in a
single fast/cheap call. That is exactly what the router needs: classify a request
by mode (direct vs orchestrated), complexity, category and whether it needs scope
confirmation — all at once, with confidence to escalate when Jev is unsure.

Design: a small pluggable contract. `JevClassifier` hits the real API; when there
is no Jev key (or you want it local), `LLMClassifier` emulates the same typed
contract with the configured LLM (DeepSeek). The router consumes the same
normalized shape either way.

API shape (docs.typesafe.ai/api):
  POST https://api.typesafe.ai/v1/systemone
  Authorization: Bearer <TYPESAFE_API_KEY>
  { "model", "state", "questions": { "<name>": {"type","instructions","criteria"} } }
  -> { "model", "answers": { "<name>": {...} }, "usage": {...} }
"""

import json
import os
from typing import Any, Dict, List, Optional

TYPESAFE_URL_DEFAULT = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL_DEFAULT = "jev-latest"
TIMEOUT_DEFAULT = 30


class ClassifierError(RuntimeError):
    pass


def jev_available() -> bool:
    """True if there's a Jev key configured (so the router can prefer it)."""
    return bool((os.getenv("TYPESAFE_API_KEY") or "").strip())


# --- typed question builders (mirror Jev's schema) --------------------------

def choice(instructions: str, options: Dict[str, str]) -> Dict[str, Any]:
    """A 'pick one' question. `options` is {name: description}, 1..255 entries."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions: str, levels: List[str]) -> Dict[str, Any]:
    """A graded question. `levels` is an ordered list of level descriptions; the
    position is the score (index 0 = 0), 1..10 levels."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def boolean(instructions: str, criteria: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """A yes/no question (Jev calls it 'noul'); returns a probability 0..1."""
    q: Dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = dict(criteria)
    return q


def _normalize(answer: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse Jev's per-type answer into a common shape the router can read:
    {type, value, confidence, probabilities, ...}."""
    t = answer.get("type")
    if t == "choice":
        return {"type": "choice", "value": answer.get("choice"),
                "confidence": answer.get("confidence"),
                "probabilities": answer.get("probabilities", {})}
    if t == "score":
        return {"type": "score", "value": answer.get("score"),
                "confidence": answer.get("confidence"),
                "probabilities": answer.get("probabilities", {}),
                "legend": answer.get("legend", {})}
    if t == "noul":
        prob = answer.get("noul")
        try:
            p = float(prob)
        except (TypeError, ValueError):
            p = 0.0
        return {"type": "noul", "value": p >= 0.5, "probability": p,
                # Jev doesn't give a confidence for noul; derive one from how far
                # the probability is from the 0.5 coin-flip.
                "confidence": round(abs(p - 0.5) * 2, 4)}
    return {"type": t or "unknown", "value": answer}


# --- backends ---------------------------------------------------------------

def jev_query(state: Any, questions: Dict[str, Dict[str, Any]],
              model: Optional[str] = None, api_key: Optional[str] = None,
              url: Optional[str] = None, timeout: int = TIMEOUT_DEFAULT) -> Dict[str, Dict[str, Any]]:
    """Low-level call to Jev. Returns {question_name: normalized_answer}.
    Raises ClassifierError on missing key, HTTP errors or a malformed reply."""
    import requests

    api_key = (api_key or os.getenv("TYPESAFE_API_KEY") or "").strip()
    if not api_key:
        raise ClassifierError("Falta TYPESAFE_API_KEY (cargala en Config)")
    if not questions:
        raise ClassifierError("No hay preguntas para clasificar")

    url = url or os.getenv("TYPESAFE_API_URL") or TYPESAFE_URL_DEFAULT
    model = model or os.getenv("TYPESAFE_MODEL") or TYPESAFE_MODEL_DEFAULT
    cuerpo = {"model": model, "state": state, "questions": questions}
    try:
        r = requests.post(url, json=cuerpo, timeout=timeout,
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"})
    except Exception as e:
        raise ClassifierError(f"No pude conectarme a Jev: {e}") from e

    if r.status_code != 200:
        detalle = ""
        try:
            detalle = json.dumps(r.json().get("detail", ""), ensure_ascii=False)[:300]
        except Exception:
            detalle = (r.text or "")[:300]
        raise ClassifierError(f"Jev devolvió {r.status_code}: {detalle}")

    try:
        datos = r.json()
        answers = datos.get("answers", {})
    except Exception as e:
        raise ClassifierError(f"Respuesta de Jev ilegible: {e}") from e

    return {nombre: _normalize(a) for nombre, a in answers.items()}


class JevClassifier:
    name = "jev"

    def classify(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return jev_query(state, questions)


class LLMClassifier:
    """Fallback: emulate the typed contract with the configured LLM (DeepSeek).
    Same normalized output, with a best-effort confidence. Local to your stack:
    nothing leaves to Jev's cloud."""
    name = "llm"

    def __init__(self, provider: Any = None):
        self._provider = provider

    def _llm(self):
        if self._provider is None:
            from core.proveedores import crear_proveedor
            self._provider = crear_proveedor("deepseek") or crear_proveedor()
        return self._provider

    def classify(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        prov = self._llm()
        if prov is None:
            raise ClassifierError("No hay proveedor para clasificar (ni Jev ni LLM)")
        lineas = []
        for nombre, q in questions.items():
            t = q.get("type")
            if t == "choice":
                opciones = " | ".join(f"{k}: {v}" for k, v in (q.get("criteria") or {}).items())
                lineas.append(f'- "{nombre}" (choice; elegí UNA): {q.get("instructions","")} Opciones -> {opciones}')
            elif t == "score":
                niveles = "; ".join(f"{i}={d}" for i, d in enumerate(q.get("criteria") or []))
                lineas.append(f'- "{nombre}" (score, entero 0..{len(q.get("criteria") or [])-1}): {q.get("instructions","")} Niveles -> {niveles}')
            else:  # noul / boolean
                lineas.append(f'- "{nombre}" (boolean true/false): {q.get("instructions","")}')
        prompt = (
            "Clasificá el CONTENIDO respondiendo cada pregunta. Devolvé SOLO un "
            "objeto JSON con una clave por pregunta; cada valor es "
            '{"value": <respuesta>, "confidence": <0..1>}. Para choice value es el '
            "nombre de la opción; para score es el entero del nivel; para boolean "
            "es true/false.\n\nPreguntas:\n" + "\n".join(lineas) +
            f"\n\nContenido:\n{state}")
        resp = prov.completar(mensajes=[{"role": "user", "content": prompt}], temperature=0)
        texto = resp.texto or ""
        inicio, fin = texto.find("{"), texto.rfind("}")
        crudo = {}
        if inicio != -1 and fin > inicio:
            try:
                crudo = json.loads(texto[inicio:fin + 1])
            except (ValueError, TypeError):
                crudo = {}
        salida: Dict[str, Dict[str, Any]] = {}
        for nombre, q in questions.items():
            item = crudo.get(nombre) if isinstance(crudo, dict) else None
            valor = item.get("value") if isinstance(item, dict) else item
            conf = item.get("confidence") if isinstance(item, dict) else None
            t = q.get("type")
            if t == "noul":
                v = str(valor).strip().lower() in ("true", "sí", "si", "yes", "1")
                salida[nombre] = {"type": "noul", "value": v,
                                  "probability": 1.0 if v else 0.0,
                                  "confidence": conf if conf is not None else 0.5}
            elif t == "score":
                try:
                    v = float(valor)
                except (TypeError, ValueError):
                    v = 0.0
                salida[nombre] = {"type": "score", "value": v, "confidence": conf, "probabilities": {}}
            else:
                salida[nombre] = {"type": "choice", "value": valor, "confidence": conf, "probabilities": {}}
        return salida


def make_classifier(prefer_local: bool = False) -> Any:
    """Jev if there's a key (and not forced local); otherwise the LLM fallback."""
    if jev_available() and not prefer_local:
        return JevClassifier()
    return LLMClassifier()


# --- router convenience ------------------------------------------------------

# Taxonomía por defecto para ciberseguridad (ajustable).
CATEGORIES = {
    "recon": "Reconocimiento / OSINT pasivo sobre un objetivo",
    "escaneo": "Escaneo activo de puertos, servicios o superficie",
    "analisis_vuln": "Análisis o evaluación de vulnerabilidades",
    "analisis_logs": "Análisis de logs, tráfico o forense",
    "reversing_malware": "Ingeniería inversa o análisis de malware/binarios",
    "exploit_poc": "Desarrollo o uso de exploits / prueba de concepto",
    "sast": "Revisión de código fuente en busca de fallos",
    "informe": "Redacción de informe, documentación o resumen",
    "otro": "Cualquier otra cosa",
}


def router_questions() -> Dict[str, Dict[str, Any]]:
    return {
        "mode": choice(
            "¿La tarea se resuelve con una sola acción o necesita coordinar varios pasos?",
            {"directo": "Una sola consulta/acción puntual, no hace falta dividir",
             "orquestado": "Requiere varios pasos o sub-tareas coordinadas"}),
        "complexity": score(
            "¿Qué tan compleja es la tarea?",
            ["Trivial o inmediata", "Media", "Compleja, multi-paso"]),
        "category": choice(
            "¿A qué tipo de trabajo de ciberseguridad corresponde?", CATEGORIES),
        "needs_scope": boolean(
            "¿Es una acción ACTIVA contra un objetivo (escaneo/explotación) que "
            "requiere autorización/alcance confirmado, en vez de análisis pasivo?"),
    }


def classify_task(text: str, classifier: Any = None,
                  threshold: float = 0.6) -> Dict[str, Any]:
    """Clasifica un pedido para el router. Devuelve una decisión normalizada.
    Si la confianza en el modo es baja (< threshold), cae a 'orquestado' (más
    seguro). Nunca levanta: ante cualquier error, orquesta por defecto."""
    clf = classifier or make_classifier()
    try:
        ans = clf.classify(text, router_questions())
    except Exception as e:
        return {"mode": "orquestado", "category": "otro", "complexity": None,
                "needs_scope": False, "backend": getattr(clf, "name", "?"),
                "confianza_baja": True, "error": str(e), "raw": {}}

    modo = (ans.get("mode") or {})
    conf = modo.get("confidence")
    valor_modo = modo.get("value") or "orquestado"
    baja = conf is not None and conf < threshold
    if baja:
        valor_modo = "orquestado"   # ante la duda, orquestar
    scope = (ans.get("needs_scope") or {})
    return {
        "mode": valor_modo,
        "mode_confidence": conf,
        "complexity": (ans.get("complexity") or {}).get("value"),
        "category": (ans.get("category") or {}).get("value"),
        "needs_scope": bool(scope.get("value")),
        "scope_probability": scope.get("probability"),
        "backend": getattr(clf, "name", "?"),
        "confianza_baja": bool(baja),
        "raw": ans,
    }
