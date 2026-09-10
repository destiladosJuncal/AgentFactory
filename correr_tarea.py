#!/usr/bin/env python3
"""
Runner headless de una tarea programada. Lo invoca launchd (no la UI):

    <venv>/bin/python3  correr_tarea.py --tarea <id>

Carga la conversación de la tarea, le manda el prompt guardado al agente
(un turno completo, con herramientas), la respuesta queda persistida en la
conversación, y te avisa con una notificación de macOS. No necesita la app
abierta ni ninguna ventana.
"""

import argparse
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

from core import rutas  # noqa: E402
rutas.cargar_env()

from core.conversaciones import GestorConversaciones  # noqa: E402
from core.chat import ConversacionChat  # noqa: E402
from core import programador  # noqa: E402


def notificar(titulo: str, mensaje: str):
    """Notificación nativa de macOS. Best-effort: nunca rompe la corrida."""
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--tarea", required=True)
    args = parser.parse_args()

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
