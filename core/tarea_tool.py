"""
Tools del chat para PROGRAMAR tareas (el "cron" de AgentFactory) desde la
conversación.

Idea central (script-first): cuando la persona pide una tarea concreta y
recurrente —un workflow—, el agente escribe y prueba UN script determinístico y
lo registra acá. Ese script queda como tarea programada y corre solo, barato,
sin re-invocar al modelo. El LLM solo se llama de nuevo si el script falla o
pide escalar (imprime 'ESCALAR: <motivo>'), como fallback.
"""

from typing import Any, Dict, List

from core import programador as prog


def programar_tarea_script(conversacion: str, titulo: str, descripcion: str, codigo: str,
                           intervalo_minutos: int = None, hora: int = None,
                           minuto: int = None) -> Dict[str, Any]:
    if not codigo or not codigo.strip():
        return {"error": "Falta el código del script (workflow determinístico)."}
    if not intervalo_minutos and hora is None:
        return {"error": "Definí la frecuencia: intervalo_minutos (cada N min) o hora+minuto (diario)."}
    r = prog.crear_tarea(
        titulo=titulo or "Tarea", conversacion=conversacion,
        descripcion=descripcion or "", codigo=codigo, tipo="script",
        intervalo_minutos=intervalo_minutos,
        hora=hora or 0, minuto=minuto or 0)
    if "error" in r:
        return r
    return {"programada": r["id"], "titulo": r["titulo"], "cuando": prog.describir(r),
            "aviso": "Corre sola y determinística; la ves en la pestaña Tareas."}


def listar_tareas_programadas() -> Dict[str, Any]:
    return {"tareas": [
        {"id": t["id"], "titulo": t["titulo"], "cuando": prog.describir(t),
         "tipo": t.get("tipo", "agente")} for t in prog.listar_tareas()]}


def borrar_tarea_programada(tarea_id: str) -> Dict[str, Any]:
    if not tarea_id:
        return {"error": "Falta tarea_id."}
    prog.borrar_tarea(tarea_id)
    return {"borrada": tarea_id}


def ejecutar_tool_tarea(nombre: str, argumentos: dict, conversacion: str = None) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "programar_tarea_script":
        return programar_tarea_script(
            conversacion, a.get("titulo"), a.get("descripcion"), a.get("codigo"),
            a.get("intervalo_minutos"), a.get("hora"), a.get("minuto"))
    if nombre == "listar_tareas_programadas":
        return listar_tareas_programadas()
    if nombre == "borrar_tarea_programada":
        return borrar_tarea_programada(a.get("tarea_id"))
    return {"error": f"Tool de tarea desconocida: {nombre}"}


TOOLS_SCHEMA_TAREA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "programar_tarea_script",
            "description": (
                "Registra un WORKFLOW determinístico como tarea programada (aparece "
                "en la pestaña Tareas y corre solo, sin gastar modelo). Usalo cuando "
                "la persona pide una tarea concreta y recurrente. ANTES de llamarlo: "
                "escribí el script y PROBALO con ejecutar_python hasta que ande. El "
                "script debe imprimir su resultado por stdout; si no puede determinar "
                "el próximo paso, que imprima una línea 'ESCALAR: <motivo>' (ahí, y "
                "solo ahí, se invoca al agente como fallback). Frecuencia: pasá "
                "intervalo_minutos (cada N min) O hora+minuto (diario)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "nombre corto que verá la persona"},
                    "descripcion": {"type": "string", "description": "qué hace, en una frase"},
                    "codigo": {"type": "string", "description": "el script Python completo del workflow"},
                    "intervalo_minutos": {"type": "integer", "description": "correr cada N minutos"},
                    "hora": {"type": "integer", "description": "hora (0-23) si es diaria"},
                    "minuto": {"type": "integer", "description": "minuto (0-59) si es diaria"},
                },
                "required": ["titulo", "descripcion", "codigo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_tareas_programadas",
            "description": "Lista las tareas programadas (id, título, cuándo corre, tipo).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_tarea_programada",
            "description": "Borra una tarea programada por su id (deja de correr).",
            "parameters": {
                "type": "object",
                "properties": {"tarea_id": {"type": "string"}},
                "required": ["tarea_id"],
            },
        },
    },
]
