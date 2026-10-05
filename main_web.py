#!/usr/bin/env python3
"""
Punto de entrada de la UI web (pywebview). Alternativa a main_ui.py (Tkinter).

Se lanza aparte y convive con la UI vieja mientras la migración está en curso:

    <python> main_web.py          # o  AGENTE_UI=web  desde el arrancador

pywebview es el loop principal (en macOS la UI va en el hilo principal), por eso
NO puede convivir con Tkinter en el mismo proceso: es una app o la otra.
"""

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from core import rutas
rutas.cargar_env()

try:
    import webview
    from webui.puente import Puente
except ImportError as e:
    print("Faltan las dependencias de la UI web.\n"
          "Instalalas con:\n\n"
          f"    {sys.executable} -m pip install -r requirements-web.txt\n\n"
          f"(detalle: {e})")
    sys.exit(1)

INDEX = str(APP_DIR / "webui" / "index.html")


def main():
    puente = Puente()
    ventana = webview.create_window(
        "AgentFactory", url=INDEX, js_api=puente,
        width=1100, height=760, min_size=(820, 520),
        text_select=True)            # clave: pywebview la trae apagada
    puente.window = ventana
    webview.start()


if __name__ == "__main__":
    main()
