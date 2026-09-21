"""
Backend del programador para macOS: launchd, vía LaunchAgents por usuario.

Esto es el código que vivía adentro de core/programador.py. Se movió tal cual
cuando se agregó Windows: la lógica no cambió, solo dejó de ser la única.

No necesita sudo: los LaunchAgents por-usuario se cargan con `launchctl`
normal, en el dominio gui/<uid>.
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from core import interprete
from core.rutas import dir_app, dir_datos

LABEL_PREFIX = "local.agentfactory.tarea."


def nombre_tarea(tarea_id: str) -> str:
    return LABEL_PREFIX + tarea_id


def _dir_launch_agents() -> Path:
    d = Path.home() / "Library" / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _plist_path(tarea_id: str) -> Path:
    return _dir_launch_agents() / f"{nombre_tarea(tarea_id)}.plist"


def _plist_xml(tarea: Dict[str, Any]) -> str:
    label = nombre_tarea(tarea["id"])
    py = interprete.interpreter()
    script = str(dir_app() / "correr_tarea.py")
    logdir = dir_datos() / "_tareas"
    logdir.mkdir(parents=True, exist_ok=True)
    log = str(logdir / f"{tarea['id']}.log")

    intervalo = tarea.get("intervalo_minutos")
    if intervalo:
        # "cada N minutos" → StartInterval (en segundos). Independiente de la hora.
        cal_xml = f"    <key>StartInterval</key>\n    <integer>{int(intervalo) * 60}</integer>\n"
    else:
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
    return subprocess.run(["launchctl", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def instalar(tarea: Dict[str, Any]) -> Optional[str]:
    plist = _plist_path(tarea["id"])
    plist.write_text(_plist_xml(tarea), encoding="utf-8")
    label = nombre_tarea(tarea["id"])
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


def desinstalar(tarea_id: str) -> None:
    label = nombre_tarea(tarea_id)
    uid = os.getuid()
    _launchctl("bootout", f"gui/{uid}/{label}")
    plist = _plist_path(tarea_id)
    if plist.exists():
        _launchctl("unload", str(plist))  # inofensivo si bootout ya lo sacó
        try:
            plist.unlink()
        except OSError:
            pass


def estado(tarea_id: str) -> str:
    r = _launchctl("list", nombre_tarea(tarea_id))
    return "programada" if r.returncode == 0 else "ausente"


def correr_ahora(tarea_id: str) -> Optional[str]:
    uid = os.getuid()
    r = _launchctl("kickstart", f"gui/{uid}/{nombre_tarea(tarea_id)}")
    if r.returncode != 0:
        return (r.stderr or r.stdout or "").strip()[:300]
    return None
