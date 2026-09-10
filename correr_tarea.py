#!/usr/bin/env python3
"""
Runner headless de una tarea programada. Lo invoca el planificador del sistema
(launchd en macOS, el Programador de tareas en Windows), no la UI:

    <python> correr_tarea.py --tarea <id> [--datos <carpeta> --app <carpeta>]

Carga la conversación de la tarea, le manda el prompt guardado al agente
(un turno completo, con herramientas) y la respuesta queda persistida en la
conversación. No necesita la app abierta ni ninguna ventana.

Sobre --datos y --app: en macOS el LaunchAgent puede inyectar variables de
entorno y las pasa por ahí. El Programador de tareas de Windows NO sabe hacer
eso, así que las mismas rutas viajan como argumentos. Por eso el parseo tiene
que ocurrir ANTES de importar core.rutas, que decide dónde vive todo en cuanto
se lo importa.
"""

import argparse
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)


def _argumentos():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tarea", required=True)
    parser.add_argument("--datos", default=None,
                        help="carpeta de datos (equivale a AGENTE_DATOS)")
    parser.add_argument("--app", default=None,
                        help="carpeta del código (equivale a AGENTE_APP)")
    return parser.parse_args()


def _preparar_salida(carpeta_datos: Path, tarea_id: str):
    """Redirige stdout/stderr a un archivo de log.

    Dos motivos, y los dos rompen la corrida si no se hace:

      · Con pythonw.exe (Windows, para que no aparezca una ventana negra en
        cada corrida) `sys.stdout` es None, y el primer print() levanta
        AttributeError.
      · En una consola de Windows la codificación local no puede representar
        los emoji de los mensajes y print() muere con UnicodeEncodeError.

    De paso, el log queda en UTF-8 y al lado del historial de corridas."""
    try:
        carpeta = carpeta_datos / "_tareas"
        carpeta.mkdir(parents=True, exist_ok=True)
        log = carpeta / f"{tarea_id}.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.unlink()
        salida = open(log, "a", encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = salida
    except OSError:
        pass


args = _argumentos()
if args.datos:
    os.environ["AGENTE_DATOS"] = args.datos
if args.app:
    os.environ["AGENTE_APP"] = args.app

from core import rutas  # noqa: E402
rutas.cargar_env()

_preparar_salida(rutas.dir_datos(), args.tarea)

from core.conversaciones import GestorConversaciones  # noqa: E402
from core.chat import ConversacionChat  # noqa: E402
from core import programador  # noqa: E402


def notificar(titulo: str, mensaje: str):
    """Aviso al sistema. Best-effort: nunca rompe la corrida.

    Solo macOS por ahora. En Windows el toast nativo necesita un
    AppUserModelID registrado y una vuelta por WinRT; mientras tanto el
    resultado de cada corrida se ve en la pestaña Tareas, que es el canal
    que la persona realmente mira."""
    if sys.platform != "darwin":
        return
    import subprocess
    m = (mensaje or "").replace('"', "'")[:240]
    t = (titulo or "AgentFactory").replace('"', "'")[:80]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{m}" with title "AgentFactory" subtitle "{t}"'],
            capture_output=True, timeout=15)
    except Exception:
        pass


def main():
    tarea = next((t for t in programador.cargar() if t.get("id") == args.tarea), None)
    if tarea is None:
        print(f"No existe la tarea {args.tarea}", file=sys.stderr)
        sys.exit(1)

    print(f"▶ Tarea «{tarea['titulo']}» — {programador.describir(tarea)}", flush=True)

    gestor = GestorConversaciones()
    conv_dir = gestor.cargar_conversacion(tarea["conversacion"])
    if conv_dir is None:
        # Si la borraron, la recreamos con el mismo nombre para no perder la tarea.
        conv_dir = gestor.crear_conversacion(tarea["conversacion"])

    conversacion = ConversacionChat(conv_dir)

    # Marca de encabezado para distinguir en el historial que esto lo disparó el cron.
    prompt = f"[Tarea programada · {programador.describir(tarea)}]\n\n{tarea['prompt']}"
    try:
        respuesta = conversacion.enviar(prompt)
    except Exception as e:
        programador.registrar_corrida(tarea["id"], ok=False, error=str(e))
        notificar(tarea["titulo"], f"Falló: {e}")
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    programador.registrar_corrida(tarea["id"], ok=True, respuesta=respuesta)
    resumen = (respuesta or "").strip().splitlines()
    primera = next((l for l in resumen if l.strip()), "Listo")
    notificar(tarea["titulo"], primera[:200])
    print("✅ Listo. Respuesta guardada en la conversación.", flush=True)


if __name__ == "__main__":
    main()
