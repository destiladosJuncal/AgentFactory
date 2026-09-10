"""
Ícono de la barra de tareas en Windows.

Es el espejo de core/mac_icono.py, con el mismo contrato: best-effort, nunca
levanta, y devuelve un texto si no pudo (para el panel de diagnóstico).

Hacen falta dos cosas distintas, y por eso no alcanza con el iconphoto que ya
usa main_ui.py:

  · La barra de tareas y Alt-Tab leen un .ico de verdad (wm iconbitmap), no el
    PNG que sirve para la barra de título.
  · El AGRUPAMIENTO en la barra de tareas lo decide el AppUserModelID. Sin uno
    propio, Windows agrupa la ventana bajo el intérprete y la persona ve
    "Python" en vez de AgentFactory.
"""

import sys
from pathlib import Path
from typing import Optional

from core import plataforma

APP_ID = "AgentFactory.Escritorio.1"


def poner_icono_taskbar(ventana, ico: Optional[Path] = None) -> Optional[str]:
    """Aplica el ícono. Devuelve None si salió bien, o el motivo si no."""
    if not plataforma.ES_WINDOWS:
        return "solo aplica en Windows"

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        # No es fatal: solo cambia cómo agrupa la barra de tareas.
        pass

    if ico is None:
        return "sin archivo .ico"
    ico = Path(ico)
    if not ico.exists():
        return f"no encontré {ico}"

    try:
        ventana.iconbitmap(default=str(ico))
    except Exception as e:
        return f"no pude aplicar el ícono: {e}"
    return None


def generar_ico(png: Path, destino: Path) -> Optional[str]:
    """Arma el .ico multi-resolución a partir del PNG de la marca.

    Se corre una sola vez y el .ico se versiona junto al código; está acá para
    poder regenerarlo si cambia el logo."""
    try:
        from PIL import Image
    except ImportError:
        return "falta Pillow"
    try:
        imagen = Image.open(png)
        imagen.save(destino, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                    (64, 64), (128, 128), (256, 256)])
    except Exception as e:
        return f"no pude generar el .ico: {e}"
    return None


if __name__ == "__main__":
    raiz = Path(__file__).resolve().parent.parent
    error = generar_ico(raiz / "agentfactory-icon-1024.png", raiz / "icono.ico")
    print(error or f"icono.ico generado en {raiz}")
    sys.exit(1 if error else 0)
