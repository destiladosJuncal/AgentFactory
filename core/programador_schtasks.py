"""
Backend del programador para Windows: el Task Scheduler, vía schtasks.exe.

Es el equivalente de los LaunchAgents de macOS: tareas por-usuario, sin
permisos de administrador y sin contraseña guardada.

## Por qué XML y no `schtasks /SC`

La forma corta (`/SC WEEKLY /D MON,TUE /ST 09:00`) parece más simple y tiene
dos trampas que rompen tareas sin avisar:

  1. **Las abreviaturas de día están localizadas.** En un Windows en español,
     schtasks no espera `MON,TUE,WED` sino `LUN,MAR,MIÉ`. Pasarle las
     inglesas hace fallar la creación —o peor, crearla en días equivocados—
     y el mensaje de error no dice que el problema es el idioma.
  2. **`/ST` sigue el formato de hora corto del sistema**, que también cambia
     con la configuración regional.

El XML del Task Scheduler es independiente del idioma. Y de paso permite tocar
opciones que `/SC` no expone, tres de las cuales importan de verdad:

  · `DisallowStartIfOnBatteries` viene en **true** por defecto: en una laptop
    sin enchufar, la tarea NO corre y no queda registro de por qué.
  · `StopIfGoingOnBatteries`, lo mismo a mitad de camino.
  · `StartWhenAvailable` hace que una corrida perdida (máquina apagada) se
    ejecute al prender, que es como se comporta launchd en la Mac.

## Por qué no hay un .cmd envolvente

schtasks no sabe pasar variables de entorno, y las tareas necesitan
AGENTE_DATOS y AGENTE_APP. La salida obvia es generar un .cmd que las setee y
llame a Python, pero un .cmd es un proceso de consola: con `InteractiveToken`
el Task Scheduler le hace parpadear una ventana negra a la persona en cada
corrida. En vez de eso, las rutas se pasan como argumentos a correr_tarea.py y
se lanza pythonw.exe, que no tiene ventana. De paso desaparece el enredo de
comillas anidadas de `/TR`.
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from core import interprete, plataforma
from core.rutas import dir_app, dir_datos

PREFIJO = "AgentFactory-"

# Índice = día tal como lo guarda el resto del programa (0=domingo).
DIAS_XML = ("Sunday", "Monday", "Tuesday", "Wednesday",
            "Thursday", "Friday", "Saturday")


def nombre_tarea(tarea_id: str) -> str:
    return f"{PREFIJO}{tarea_id}"


def _xml_path(tarea_id: str) -> Path:
    d = dir_datos() / "_tareas"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tarea_id}.xml"


def _usuario() -> str:
    """El usuario dueño de la tarea, como lo espera el Task Scheduler."""
    dominio = os.environ.get("USERDOMAIN", "")
    usuario = os.environ.get("USERNAME", "")
    return f"{dominio}\\{usuario}" if dominio else usuario


def _schtasks(*args) -> subprocess.CompletedProcess:
    # La salida de schtasks viene en la codepage OEM (acá cp850), no en la
    # ANSI que asumiría text=True: sin esto, cualquier acento vuelve roto.
    return subprocess.run(
        ["schtasks", *args], capture_output=True, text=True,
        encoding=plataforma.codificacion_consola(), errors="replace")


def _disparador(tarea: Dict[str, Any]) -> str:
    intervalo = tarea.get("intervalo_minutos")
    if intervalo:
        # "cada N minutos": disparador diario que se repite cada N min todo el día.
        return (f"    <CalendarTrigger>\n"
                f"      <StartBoundary>2000-01-01T00:00:00</StartBoundary>\n"
                f"      <Enabled>true</Enabled>\n"
                f"      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                f"      <Repetition>\n"
                f"        <Interval>PT{int(intervalo)}M</Interval>\n"
                f"        <Duration>P1D</Duration>\n"
                f"        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
                f"      </Repetition>\n"
                f"    </CalendarTrigger>\n")

    hora, minuto = int(tarea["hora"]), int(tarea["minuto"])
    # StartBoundary necesita una fecha; se usa la de creación. Solo marca
    # desde cuándo vale el disparador, no el día en que corre.
    inicio = f"2000-01-01T{hora:02d}:{minuto:02d}:00"

    dias = tarea.get("dias")
    if dias:
        nombres = "".join(f"<{DIAS_XML[int(d)]} />" for d in sorted(set(dias)))
        agenda = (f"      <ScheduleByWeek>\n"
                  f"        <WeeksInterval>1</WeeksInterval>\n"
                  f"        <DaysOfWeek>{nombres}</DaysOfWeek>\n"
                  f"      </ScheduleByWeek>\n")
    else:
        agenda = ("      <ScheduleByDay>\n"
                  "        <DaysInterval>1</DaysInterval>\n"
                  "      </ScheduleByDay>\n")

    return (f"    <CalendarTrigger>\n"
            f"      <StartBoundary>{inicio}</StartBoundary>\n"
            f"      <Enabled>true</Enabled>\n"
            f"{agenda}"
            f"    </CalendarTrigger>\n")


def _xml(tarea: Dict[str, Any]) -> str:
    python = interprete.interpreter_no_console()
    script = str(dir_app() / "correr_tarea.py")
    argumentos = (f'"{script}" --tarea {tarea["id"]} '
                  f'--datos "{dir_datos()}" --app "{dir_app()}"')

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(tarea.get("titulo") or "Tarea de AgentFactory")}</Description>
    <URI>\\{nombre_tarea(tarea["id"])}</URI>
  </RegistrationInfo>
  <Triggers>
{_disparador(tarea)}  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(_usuario())}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>{escape(argumentos)}</Arguments>
      <WorkingDirectory>{escape(str(dir_app()))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def instalar(tarea: Dict[str, Any]) -> Optional[str]:
    """Registra la tarea. Devuelve un mensaje de error, o None si salió bien."""
    ruta = _xml_path(tarea["id"])
    try:
        # UTF-16 con BOM, no UTF-8: schtasks /XML rechaza el archivo con un
        # "The task XML is malformed" que no explica nada si la codificación
        # no es la que espera. Es el modo de falla más común de esta ruta.
        ruta.write_text(_xml(tarea), encoding="utf-16")
    except OSError as e:
        return f"No pude escribir la definición de la tarea: {e}"

    r = _schtasks("/Create", "/TN", nombre_tarea(tarea["id"]),
                  "/XML", str(ruta), "/F")
    if r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()
        return f"El Programador de tareas de Windows la rechazó: {detalle[:300]}"
    return None


def desinstalar(tarea_id: str) -> None:
    # Si ya no está, schtasks devuelve != 0; no es un error para nosotros.
    _schtasks("/Delete", "/TN", nombre_tarea(tarea_id), "/F")
    ruta = _xml_path(tarea_id)
    if ruta.exists():
        try:
            ruta.unlink()
        except OSError:
            pass


def estado(tarea_id: str) -> str:
    """'programada' si el sistema la conoce, 'ausente' si no."""
    r = _schtasks("/Query", "/TN", nombre_tarea(tarea_id))
    return "programada" if r.returncode == 0 else "ausente"


def correr_ahora(tarea_id: str) -> Optional[str]:
    """Dispara la tarea a mano, sin esperar el horario."""
    r = _schtasks("/Run", "/TN", nombre_tarea(tarea_id))
    if r.returncode != 0:
        return (r.stderr or r.stdout or "").strip()[:300]
    return None
