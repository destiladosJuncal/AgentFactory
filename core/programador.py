"""
Programador de tareas recurrentes (el "cron" de AgentFactory).

El agente no puede despertarse solo: quien lo despierta es el SISTEMA. En macOS
eso es launchd. Cada tarea programada se traduce en un LaunchAgent por-usuario
(un .plist en ~/Library/LaunchAgents) que, a la hora indicada, corre:

    <venv>/bin/python3  <app>/correr_tarea.py --tarea <id>

Ese runner headless carga la conversación, le manda el prompt guardado al agente
(ConversacionChat.enviar), deja la respuesta en la conversación y te avisa con una
notificación de macOS. No hace falta que la app esté abierta.

Las tareas se guardan en $DATOS/tareas.json (con los datos, no con el código).
No necesita sudo: los LaunchAgents por-usuario se cargan con `launchctl` normal.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.rutas import dir_datos, dir_app

LABEL_PREFIX = "local.agentfactory.tarea."


def _tareas_path() -> Path:
    return dir_datos() / "tareas.json"


def _dir_launch_agents() -> Path:
    d = Path.home() / "Library" / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _python() -> str:
    cand = dir_app() / "venv" / "bin" / "python3"
    return str(cand) if cand.exists() else sys.executable


def _plist_path(tarea_id: str) -> Path:
    return _dir_launch_agents() / f"{LABEL_PREFIX}{tarea_id}.plist"


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


def _plist_xml(tarea: Dict[str, Any]) -> str:
    label = LABEL_PREFIX + tarea["id"]
    py = _python()
    script = str(dir_app() / "correr_tarea.py")
    logdir = dir_datos() / "_tareas"
    logdir.mkdir(parents=True, exist_ok=True)
    log = str(logdir / f"{tarea['id']}.log")

    # StartCalendarInterval: sin Weekday = todos los días.
    cal = f"        <key>Hour</key><integer>{int(tarea['hora'])}</integer>\n" \
          f"        <key>Minute</key><integer>{int(tarea['minuto'])}</integer>\n"
    dias = tarea.get("dias")  # lista 0-6 (0=domingo), opcional
    if dias:
        bloques = "".join(
            "    <dict>\n"
            f"        <key>Weekday</key><integer>{int(d)}</integer>\n{cal}"
            "    </dict>\n" for d in dias)
        cal_xml = f"    <key>StartCalendarInterval</key>\n    <array>\n{bloques}    </array>\n"
    else:
        cal_xml = f"    <key>StartCalendarInterval</key>\n    <dict>\n{cal}    </dict>\n"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{script}</string>
        <string>--tarea</string>
        <string>{tarea['id']}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AGENTE_DATOS</key><string>{dir_datos()}</string>
        <key>AGENTE_APP</key><string>{dir_app()}</string>
    </dict>
{cal_xml}    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _cargar_en_launchd(tarea: Dict[str, Any]) -> Optional[str]:
    plist = _plist_path(tarea["id"])
    plist.write_text(_plist_xml(tarea), encoding="utf-8")
    label = LABEL_PREFIX + tarea["id"]
    uid = os.getuid()
    # bootout por las dudas (si ya existía), luego bootstrap del dominio gui/<uid>.
    _launchctl("bootout", f"gui/{uid}/{label}")
    r = _launchctl("bootstrap", f"gui/{uid}", str(plist))
    if r.returncode != 0:
        # Fallback al API viejo (load) para macOS donde bootstrap se comporta distinto.
        r2 = _launchctl("load", "-w", str(plist))
        if r2.returncode != 0:
            return (r.stderr or r2.stderr or "launchctl falló").strip()
    return None


def _descargar_de_launchd(tarea_id: str):
    label = LABEL_PREFIX + tarea_id
    uid = os.getuid()
    _launchctl("bootout", f"gui/{uid}/{label}")
    plist = _plist_path(tarea_id)
    if plist.exists():
        _launchctl("unload", str(plist))  # inofensivo si bootout ya lo sacó


def crear_tarea(titulo: str, conversacion: str, prompt: str,
                hora: int, minuto: int, dias: Optional[List[int]] = None) -> Dict[str, Any]:
    tid = time.strftime("%Y%m%d%H%M%S")
    tarea = {
        "id": tid, "titulo": titulo or "Tarea",
        "conversacion": conversacion, "prompt": prompt,
        "hora": int(hora), "minuto": int(minuto),
        "dias": dias or None,
        "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ultima_corrida": None,
    }
    error = _cargar_en_launchd(tarea)
    if error:
        return {"error": error}
    tareas = cargar()
    tareas.append(tarea)
    _guardar(tareas)
    return tarea


def borrar_tarea(tarea_id: str) -> bool:
    _descargar_de_launchd(tarea_id)
    plist = _plist_path(tarea_id)
    if plist.exists():
        plist.unlink()
    runs = _runs_path(tarea_id)
    if runs.exists():
        runs.unlink()
    tareas = [t for t in cargar() if t.get("id") != tarea_id]
    _guardar(tareas)
    return True


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
    hhmm = f"{int(tarea['hora']):02d}:{int(tarea['minuto']):02d}"
    if tarea.get("dias"):
        nombres = ["Do", "Lu", "Ma", "Mi", "Ju", "Vi", "Sa"]
        d = " ".join(nombres[i] for i in tarea["dias"])
        return f"{d} a las {hhmm}"
    return f"todos los días a las {hhmm}"
