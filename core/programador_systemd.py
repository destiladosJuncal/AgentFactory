"""
Backend del programador para Linux: systemd, vía timers de usuario.

Debian, Ubuntu, Fedora, RHEL, openSUSE y derivados usan systemd, así que un
solo backend cubre "Linux" sin ramificar por distro. Se usan **timers de
usuario** (`systemctl --user`): no necesitan sudo y viven en
~/.config/systemd/user/, junto al resto de la config del usuario.

Cada tarea son dos units:
  · <base>.service  — Type=oneshot, corre correr_tarea.py --tarea <id>
  · <base>.timer    — cuándo dispararlo (intervalo o calendario)

A diferencia de schtasks en Windows, systemd NO localiza los nombres de día
(siempre Mon,Tue,…), así que el calendario es idéntico en cualquier idioma.

Nota sobre correr con la sesión cerrada: por defecto los timers de usuario solo
corren mientras hay sesión. Para que sigan con el usuario deslogueado hace falta
`loginctl enable-linger $USER` (una vez). Se documenta; no se fuerza, porque
habilitar linger es una decisión del usuario.
"""

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from core import interprete
from core.rutas import dir_app, dir_datos

LABEL_PREFIX = "agentfactory-tarea-"
_DIAS_SYSTEMD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]  # 0 = domingo


def nombre_tarea(tarea_id: str) -> str:
    return f"{LABEL_PREFIX}{tarea_id}"


def _dir_units() -> Path:
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _systemctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _disponible() -> bool:
    """¿Hay una instancia de systemd de usuario con la que hablar? En un
    contenedor sin systemd, o por SSH sin sesión de usuario, puede no haberla."""
    try:
        return _systemctl("show-environment").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _texto_service(tarea: Dict[str, Any]) -> str:
    py = interprete.interprete()
    script = str(dir_app() / "correr_tarea.py")
    desc = (tarea.get("titulo") or "Tarea").replace("\n", " ")[:180]
    return (
        "[Unit]\n"
        f"Description=AgentFactory: {desc}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "Environment=PYTHONIOENCODING=utf-8\n"
        f'ExecStart="{py}" "{script}" --tarea {tarea["id"]} '
        f'--datos "{dir_datos()}" --app "{dir_app()}"\n'
    )


def _texto_timer(tarea: Dict[str, Any]) -> str:
    desc = (tarea.get("titulo") or "Tarea").replace("\n", " ")[:180]
    intervalo = tarea.get("intervalo_minutos")
    if intervalo:
        n = int(intervalo)
        agenda = f"OnBootSec={n}min\nOnUnitActiveSec={n}min\n"
    else:
        hh, mm = int(tarea["hora"]), int(tarea["minuto"])
        dias = tarea.get("dias")
        if dias:
            nombres = ",".join(_DIAS_SYSTEMD[int(d)] for d in sorted(set(dias)))
            agenda = f"OnCalendar={nombres} *-*-* {hh:02d}:{mm:02d}:00\n"
        else:
            agenda = f"OnCalendar=*-*-* {hh:02d}:{mm:02d}:00\n"
    return (
        "[Unit]\n"
        f"Description=AgentFactory timer: {desc}\n\n"
        "[Timer]\n"
        f"{agenda}"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def instalar(tarea: Dict[str, Any]) -> Optional[str]:
    if not _disponible():
        return ("No encontré systemd de usuario (`systemctl --user`). Las tareas "
                "programadas en Linux lo necesitan. Si estás en un entorno sin "
                "sesión de usuario systemd, no se puede agendar automáticamente.")
    base = nombre_tarea(tarea["id"])
    d = _dir_units()
    try:
        (d / f"{base}.service").write_text(_texto_service(tarea), encoding="utf-8")
        (d / f"{base}.timer").write_text(_texto_timer(tarea), encoding="utf-8")
    except OSError as e:
        return f"No pude escribir los units de systemd: {e}"

    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", f"{base}.timer")
    if r.returncode != 0:
        return (r.stderr or r.stdout or "systemctl enable falló").strip()[:400]
    return None


def desinstalar(tarea_id: str) -> None:
    base = nombre_tarea(tarea_id)
    _systemctl("disable", "--now", f"{base}.timer")
    d = _dir_units()
    for suf in (".timer", ".service"):
        f = d / f"{base}{suf}"
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass
    _systemctl("daemon-reload")


def estado(tarea_id: str) -> str:
    if not _disponible():
        return "desconocido"
    r = _systemctl("is-enabled", f"{nombre_tarea(tarea_id)}.timer")
    return "programada" if r.returncode == 0 else "ausente"


def correr_ahora(tarea_id: str) -> Optional[str]:
    r = _systemctl("start", f"{nombre_tarea(tarea_id)}.service")
    if r.returncode != 0:
        return (r.stderr or r.stdout or "").strip()[:300]
    return None
