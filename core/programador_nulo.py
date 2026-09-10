"""
Backend del programador para los sistemas donde todavía no está implementado.

Devuelve un error claro en vez de fingir que anduvo. Es a propósito: guardar la
tarea en tareas.json sin registrarla en ningún lado le haría creer a la persona
que algo va a correr solo, y no correría nunca. Un "no puedo" es mejor que un
silencio.
"""

from typing import Any, Dict, Optional

from core import plataforma

MENSAJE = (f"Las tareas programadas todavía no están implementadas en "
           f"{plataforma.SISTEMA}. Funcionan en macOS (launchd) y en Windows "
           f"(Programador de tareas).")


def nombre_tarea(tarea_id: str) -> str:
    return f"agentfactory-{tarea_id}"


def instalar(tarea: Dict[str, Any]) -> Optional[str]:
    return MENSAJE


def desinstalar(tarea_id: str) -> None:
    return None


def estado(tarea_id: str) -> str:
    return "desconocido"


def correr_ahora(tarea_id: str) -> Optional[str]:
    return MENSAJE
