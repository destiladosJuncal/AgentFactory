"""
Programador de tareas recurrentes (el "cron" de AgentFactory).

El agente no puede despertarse solo: quien lo despierta es el SISTEMA. Cada
sistema tiene el suyo, así que este módulo es el frente común y la parte que
habla con el sistema vive en un backend aparte:

    macOS    core/programador_launchd.py    (LaunchAgents)
    Windows  core/programador_schtasks.py   (Programador de tareas)
    resto    core/programador_nulo.py       (todavía no implementado)

Todos exponen la misma interfaz: instalar(tarea), desinstalar(id), estado(id)
y correr_ahora(id). Lo que NO cambia entre sistemas —el archivo tareas.json,
el historial de corridas, los ids, el formato de los días— vive acá.

A la hora indicada, el sistema corre:

    <python> <app>/correr_tarea.py --tarea <id> [--datos <> --app <>]

Ese runner headless carga la conversación, le manda el prompt guardado al agente
(ConversacionChat.enviar) y deja la respuesta en la conversación. No hace falta
que la app esté abierta.

Las tareas se guardan en $DATOS/tareas.json (con los datos, no con el código).
Ningún backend necesita permisos de administrador.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import plataforma
from core.rutas import dir_datos


def _backend():
    """El backend del sistema actual. Se importa tarde a propósito: cada uno
    trae dependencias que solo existen en su sistema (os.getuid en launchd,
    winreg y schtasks en Windows)."""
    if plataforma.ES_MAC:
        from core import programador_launchd as b
    elif plataforma.ES_WINDOWS:
        from core import programador_schtasks as b
    else:
        from core import programador_nulo as b
    return b


def _tareas_path() -> Path:
    return dir_datos() / "tareas.json"


def cargar() -> List[Dict[str, Any]]:
    p = _tareas_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _guardar(tareas: List[Dict[str, Any]]):
    _tareas_path().write_text(
        json.dumps(tareas, indent=2, ensure_ascii=False), encoding="utf-8")


def listar_tareas() -> List[Dict[str, Any]]:
    return sorted(cargar(), key=lambda t: (t.get("hora", 0), t.get("minuto", 0)))


def _dir_tarea(tarea_id: str) -> Path:
    d = dir_datos() / "_tareas" / tarea_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def crear_tarea(titulo: str, conversacion: str, prompt: str = "",
                hora: int = 0, minuto: int = 0, dias: Optional[List[int]] = None,
                tipo: str = "agente", intervalo_minutos: Optional[int] = None,
                codigo: Optional[str] = None, descripcion: str = "") -> Dict[str, Any]:
    """Crea una tarea programada.

    tipo='agente' → cada corrida invoca al LLM con `prompt` (juicio/redacción).
    tipo='script' (o si pasás `codigo`) → cada corrida ejecuta un script
    determinístico; el LLM solo se llama si el script falla (fallback).
    `intervalo_minutos` = "cada N min"; si no, corre a `hora:minuto`.
    """
    tid = time.strftime("%Y%m%d%H%M%S")
    tarea = {
        "id": tid, "titulo": titulo or "Tarea",
        "conversacion": conversacion,
        "prompt": prompt or descripcion or "",
        "tipo": tipo,
        "hora": int(hora), "minuto": int(minuto),
        "dias": dias or None,
        "intervalo_minutos": int(intervalo_minutos) if intervalo_minutos else None,
        "descripcion": descripcion or "",
        "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ultima_corrida": None,
    }
    if codigo:
        tarea["tipo"] = "script"
        ruta = _dir_tarea(tid) / "tarea.py"
        ruta.write_text(codigo, encoding="utf-8")
        tarea["script"] = str(ruta)

    backend = _backend()
    error = backend.instalar(tarea)
    if error:
        return {"error": error}

    # Se guarda con qué nombre quedó registrada en el sistema: así borrarla
    # nunca depende de volver a derivar el nombre con la misma fórmula.
    tarea["backend"] = plataforma.SISTEMA
    tarea["backend_id"] = backend.nombre_tarea(tid)

    tareas = cargar()
    tareas.append(tarea)
    _guardar(tareas)
    return tarea


def borrar_tarea(tarea_id: str) -> bool:
    import shutil
    _backend().desinstalar(tarea_id)
    runs = _runs_path(tarea_id)
    if runs.exists():
        runs.unlink()
    d = dir_datos() / "_tareas" / tarea_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    tareas = [t for t in cargar() if t.get("id") != tarea_id]
    _guardar(tareas)
    return True


def recargar(tarea_id: str) -> Dict[str, Any]:
    """Reinstala la tarea en el planificador del sistema (arregla una que quedó
    'ausente', p. ej. tras copiar la carpeta de datos o un reinicio raro)."""
    t = next((x for x in cargar() if x.get("id") == tarea_id), None)
    if not t:
        return {"error": "no existe la tarea"}
    err = _backend().instalar(t)
    return {"error": err} if err else {"ok": True}


def estado_tarea(tarea_id: str) -> str:
    """'programada' | 'ausente' | 'desconocido'.

    Sirve para detectar tareas huérfanas: las que están en tareas.json pero el
    sistema ya no conoce (por ejemplo, si se copió la carpeta de datos de otra
    máquina)."""
    try:
        return _backend().estado(tarea_id)
    except Exception:
        return "desconocido"


def correr_ahora(tarea_id: str) -> Optional[str]:
    """Dispara la tarea sin esperar el horario. Devuelve un error o None."""
    try:
        return _backend().correr_ahora(tarea_id)
    except Exception as e:
        return str(e)


def marcar_corrida(tarea_id: str):
    tareas = cargar()
    for t in tareas:
        if t.get("id") == tarea_id:
            t["ultima_corrida"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _guardar(tareas)


def _runs_path(tarea_id: str) -> Path:
    d = dir_datos() / "_tareas"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tarea_id}.runs.json"


def registrar_corrida(tarea_id: str, ok: bool, respuesta: str = "", error: str = None):
    """Guarda el resultado de una corrida (para el historial de la pestaña Tareas)."""
    p = _runs_path(tarea_id)
    runs = []
    if p.exists():
        try:
            runs = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            runs = []
    runs.append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": bool(ok),
        "respuesta": (respuesta or "")[:20000],
        "error": error or None,
    })
    runs = runs[-100:]  # no crecer sin límite
    p.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
    marcar_corrida(tarea_id)


def corridas(tarea_id: str) -> List[Dict[str, Any]]:
    """Historial de corridas, la más reciente primero."""
    p = _runs_path(tarea_id)
    if not p.exists():
        return []
    try:
        return list(reversed(json.loads(p.read_text(encoding="utf-8"))))
    except Exception:
        return []


def describir(tarea: Dict[str, Any]) -> str:
    if tarea.get("intervalo_minutos"):
        return f"cada {int(tarea['intervalo_minutos'])} min"
    hhmm = f"{int(tarea['hora']):02d}:{int(tarea['minuto']):02d}"
    if tarea.get("dias"):
        nombres = ["Do", "Lu", "Ma", "Mi", "Ju", "Vi", "Sa"]
        d = " ".join(nombres[i] for i in tarea["dias"])
        return f"{d} a las {hhmm}"
    return f"todos los días a las {hhmm}"
