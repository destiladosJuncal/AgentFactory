"""
Taskbar icon on Windows.

It's the mirror of core/mac_icono.py, with the same contract: best-effort, never
raises, and returns a string if it couldn't (for the diagnostics panel).

Two different things are needed, which is why the iconphoto main_ui.py already
uses isn't enough:

  · The taskbar and Alt-Tab read a real .ico (wm iconbitmap), not the PNG that
    works for the title bar.
  · The GROUPING in the taskbar is decided by the AppUserModelID. Without one of
    our own, Windows groups the window under the interpreter and the person sees
    "Python" instead of AgentFactory.

(The error strings it returns are still Spanish on purpose: they feed the
Diagnostics panel and move to the i18n layer in a later phase.)
"""

import sys
from pathlib import Path
from typing import Optional

from core import plataforma

APP_ID = "AgentFactory.Escritorio.1"


def set_taskbar_icon(ventana, ico: Optional[Path] = None) -> Optional[str]:
    """Applies the icon. Returns None if it went well, or the reason if not."""
    if not plataforma.ES_WINDOWS:
        return "solo aplica en Windows"

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        # Not fatal: it only changes how the taskbar groups.
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


def generate_ico(png: Path, destino: Path) -> Optional[str]:
    """Builds the multi-resolution .ico from the brand PNG.

    It's run once and the .ico is versioned alongside the code; it's here so it
    can be regenerated if the logo changes."""
    try:
        from PIL import Image
    except ImportError:
        return "falta Pillow"
    try:
        image = Image.open(png)
        image.save(destino, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                   (64, 64), (128, 128), (256, 256)])
    except Exception as e:
        return f"no pude generar el .ico: {e}"
    return None


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    error = generate_ico(root / "agentfactory-icon-1024.png", root / "icono.ico")
    print(error or f"icono.ico generado en {root}")
    sys.exit(1 if error else 0)
