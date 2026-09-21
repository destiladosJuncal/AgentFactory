"""
Loading images to show them inside the conversation.

Two engines, depending on what's installed:

  · With Pillow: any format (PNG, JPEG, WEBP, GIF…) and smooth resizing. It's
    the good path and it ships in requirements.txt.
  · Without Pillow: only what Tk understands (PNG and GIF from Tk 8.6), and the
    resizing is by integer division (½, ⅓, ¼…), so an image may come out a bit
    smaller than requested. It still works and keeps the app from breaking if
    the dependency is missing.

The detail that ruins this in Tkinter: if you don't keep a live reference to the
PhotoImage, the garbage collector takes it and a white gap is left on screen.
That's why `load()` returns the object and whoever inserts it MUST hold onto it
(see `retener` in the renderer).
"""

import math
from pathlib import Path
from typing import Any, Dict, Optional

MAX_WIDTH = 520          # transcript width, in pixels
MAX_HEIGHT = 420
MAX_BYTES = 25 * 1024 * 1024

TK_EXTENSIONS = {".png", ".gif"}
PILLOW_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


def _has_pillow() -> bool:
    try:
        from PIL import Image, ImageTk  # noqa: F401
        return True
    except ImportError:
        return False


def supported_extensions() -> set:
    return PILLOW_EXTENSIONS if _has_pillow() else TK_EXTENSIONS


def looks_like_image(path: str) -> bool:
    return Path(str(path)).suffix.lower() in PILLOW_EXTENSIONS


def resolve(path: str, base: Optional[Path] = None) -> Optional[Path]:
    """Turns what the model wrote into a real path.

    It may come absolute, relative to the conversation's workspace, or with '~'.
    """
    if not path:
        return None
    # Accepts 'file:///Users/...' and paths with %20: it's how models usually
    # write them, and without this the image is "not found" even though it
    # exists.
    from core.plataforma import limpiar_ruta
    p = Path(limpiar_ruta(str(path)))
    if p.is_absolute():
        return p if p.is_file() else None
    if base:
        candidate = Path(base) / p
        if candidate.is_file():
            return candidate
    return p if p.is_file() else None


def load(path: str, base: Optional[Path] = None,
           max_width: int = MAX_WIDTH, max_height: int = MAX_HEIGHT) -> Dict[str, Any]:
    """Returns {'imagen': PhotoImage, 'ancho', 'alto', 'original', 'ruta'} or
    {'error': ...}.

    Whoever receives 'imagen' must keep a reference while it's on screen, or Tk
    deletes it."""
    file = resolve(path, base)
    if file is None:
        return {"error": f"No encontré la imagen: {path}"}

    try:
        size = file.stat().st_size
    except OSError as e:
        return {"error": f"No pude leer {file.name}: {e}"}
    if size > MAX_BYTES:
        return {"error": f"{file.name} pesa {size/1024/1024:.0f} MB; "
                         f"demasiado para mostrar en línea"}

    extension = file.suffix.lower()
    if extension not in supported_extensions():
        missing = " (instalá Pillow para más formatos)" if not _has_pillow() else ""
        return {"error": f"No puedo mostrar archivos {extension}{missing}"}

    if _has_pillow():
        return _load_pillow(file, max_width, max_height)
    return _load_tk(file, max_width, max_height)


def _load_pillow(file: Path, max_width: int, max_height: int) -> Dict[str, Any]:
    from PIL import Image, ImageTk
    try:
        img = Image.open(file)
        original = img.size
        # thumbnail keeps the aspect ratio and only shrinks, never enlarges.
        img.thumbnail((max_width, max_height), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
    except Exception as e:
        return {"error": f"No pude abrir {file.name}: {e}"}
    return {"imagen": photo, "ancho": photo.width(), "alto": photo.height(),
            "original": original, "ruta": file}


def _load_tk(file: Path, max_width: int, max_height: int) -> Dict[str, Any]:
    import tkinter as tk
    try:
        photo = tk.PhotoImage(file=str(file))
    except Exception as e:
        return {"error": f"No pude abrir {file.name}: {e}"}

    original = (photo.width(), photo.height())
    # subsample only divides by integers: the smallest factor that fits is used.
    factor = max(math.ceil(original[0] / max_width),
                 math.ceil(original[1] / max_height), 1)
    if factor > 1:
        photo = photo.subsample(factor, factor)
    return {"imagen": photo, "ancho": photo.width(), "alto": photo.height(),
            "original": original, "ruta": file}
