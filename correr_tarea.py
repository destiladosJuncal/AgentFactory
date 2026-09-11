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

    macOS: osascript. Linux: notify-send (libnotify), si está. En Windows el
    toast nativo necesita un AppUserModelID registrado y una vuelta por WinRT;
    mientras tanto el resultado de cada corrida se ve en la pestaña Tareas, que
    es el canal que la persona realmente mira."""
    import shutil
    import subprocess
    m = (mensaje or "")[:240]
    t = (titulo or "AgentFactory")[:80]
    try:
        if sys.platform == "darwin":
            ms = m.replace('"', "'"); ts = t.replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{ms}" with title "AgentFactory" subtitle "{ts}"'],
                capture_output=True, timeout=15)
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", f"AgentFactory · {t}", m],
                           capture_output=True, timeout=15)
    except Exception:
        pass


def _conversacion_de(tarea):
    gestor = GestorConversaciones()
    conv_dir = gestor.cargar_conversacion(tarea["conversacion"]) \
        or gestor.crear_conversacion(tarea["conversacion"])
    return ConversacionChat(conv_dir)


def correr_agente(tarea):
    """Tarea-agente: cada corrida invoca al LLM (para lo que necesita juicio)."""
    conversacion = _conversacion_de(tarea)
    prompt = f"[Tarea programada · {programador.describir(tarea)}]\n\n{tarea['prompt']}"
    try:
        respuesta = conversacion.enviar(prompt)
    except Exception as e:
        programador.registrar_corrida(tarea["id"], ok=False, error=str(e))
        notificar(tarea["titulo"], f"Falló: {e}")
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    programador.registrar_corrida(tarea["id"], ok=True, respuesta=respuesta)
    primera = next((l for l in (respuesta or "").splitlines() if l.strip()), "Listo")
    notificar(tarea["titulo"], primera[:200])
    print("✅ Listo. Respuesta guardada en la conversación.", flush=True)


def correr_script(tarea):
    """Tarea-script: corre el workflow DETERMINÍSTICO. El LLM solo entra si el
    script falla o pide escalar (imprime una línea 'ESCALAR: <motivo>')."""
    import subprocess
    from core import interprete
    script = tarea["script"]
    try:
        r = subprocess.run([interprete.interprete(), script], capture_output=True,
                           text=True, timeout=300, cwd=str(APP_DIR), env=os.environ.copy())
        salida, err, rc = (r.stdout or "").strip(), (r.stderr or "").strip(), r.returncode
    except Exception as e:
        salida, err, rc = "", f"no pude ejecutar el script: {e}", 1

    pidio_escalar = any(l.strip().startswith("ESCALAR:") for l in salida.splitlines())
    if rc == 0 and not pidio_escalar:
        programador.registrar_corrida(tarea["id"], ok=True, respuesta=salida or "(sin salida)")
        notificar(tarea["titulo"], (salida.splitlines() or ["Listo"])[0][:200])
        print("✅ Script OK", flush=True)
        return

    # --- Fallback: el script no pudo determinar el próximo paso → LLM ---
    motivo = next((l.split("ESCALAR:", 1)[1].strip()
                   for l in salida.splitlines() if l.strip().startswith("ESCALAR:")),
                  err or f"el script salió con código {rc}")
    print(f"⚠️ El script escaló al agente: {motivo[:200]}", flush=True)
    try:
        codigo = Path(script).read_text(encoding="utf-8")[:6000]
    except Exception:
        codigo = "(no pude leer el script)"
    conversacion = _conversacion_de(tarea)
    prompt = (
        f"[Tarea programada · {programador.describir(tarea)} · el script no pudo, resolvé o reportá]\n\n"
        f"Objetivo: {tarea.get('descripcion') or tarea.get('prompt')}\n\n"
        f"El workflow determinístico se frenó:\n{motivo[:1500]}\n\n"
        f"Código del script (en {script}):\n```python\n{codigo}\n```\n\n"
        f"Si podés, arreglá el script (reescribí ese archivo) y resolvé la tarea; "
        f"si no, explicá en una línea qué pasó.")
    try:
        respuesta = conversacion.enviar(prompt)
    except Exception as e:
        programador.registrar_corrida(
            tarea["id"], ok=False, error=f"script falló y el fallback también: {e}")
        notificar(tarea["titulo"], "Falló (script + fallback)")
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    programador.registrar_corrida(tarea["id"], ok=True,
                                  respuesta="[resuelto por el agente]\n\n" + (respuesta or ""))
    notificar(tarea["titulo"], "Resuelto por el agente (fallback)")
    print("✅ Fallback del agente resolvió.", flush=True)


def main():
    tarea = next((t for t in programador.cargar() if t.get("id") == args.tarea), None)
    if tarea is None:
        print(f"No existe la tarea {args.tarea}", file=sys.stderr)
        sys.exit(1)

    print(f"▶ Tarea «{tarea['titulo']}» — {programador.describir(tarea)}", flush=True)

    if tarea.get("tipo") == "script" and tarea.get("script"):
        correr_script(tarea)
    else:
        correr_agente(tarea)


if __name__ == "__main__":
    main()
