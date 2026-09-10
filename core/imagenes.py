"""
Carga de imágenes para mostrarlas dentro de la conversación.

Dos motores, según lo que haya instalado:

  · Con Pillow: cualquier formato (PNG, JPEG, WEBP, GIF…) y redimensionado
    suave. Es el camino bueno y viene en requirements.txt.
  · Sin Pillow: solo lo que entiende Tk (PNG y GIF desde Tk 8.6), y el
    redimensionado es por división entera (½, ⅓, ¼…), así que una imagen
    puede quedar un poco más chica de lo pedido. Sirve igual y evita que la
    app se rompa si falta la dependencia.

El detalle que arruina esto en Tkinter: si no se guarda una referencia viva al
PhotoImage, el recolector de basura se lo lleva y en pantalla queda un hueco
blanco. Por eso `cargar()` devuelve el objeto y quien lo inserta TIENE que
retenerlo (ver `retener` en el renderizador).
"""

import math
from pathlib import Path
from typing import Any, Dict, Optional

ANCHO_MAX = 520          # ancho de la transcripción, en píxeles
ALTO_MAX = 420
MAX_BYTES = 25 * 1024 * 1024

EXTENSIONES_TK = {".png", ".gif"}
EXTENSIONES_PILLOW = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


def _hay_pillow() -> bool:
    try:
        from PIL import Image, ImageTk  # noqa: F401
        return True
    except ImportError:
        return False


def extensiones_soportadas() -> set:
    return EXTENSIONES_PILLOW if _hay_pillow() else EXTENSIONES_TK


def parece_imagen(ruta: str) -> bool:
    return Path(str(ruta)).suffix.lower() in EXTENSIONES_PILLOW


def resolver(ruta: str, base: Optional[Path] = None) -> Optional[Path]:
    """Convierte lo que escribió el modelo en una ruta real.

    Puede venir absoluta, relativa al workspace de la conversación, o con '~'.
    """
    if not ruta:
        return None
    # Acepta 'file:///Users/...' y rutas con %20: es como suelen escribirlas
    # los modelos, y sin esto la imagen "no se encuentra" aunque exista.
    from core.plataforma import limpiar_ruta
    p = Path(limpiar_ruta(str(ruta)))
    if p.is_absolute():
        return p if p.is_file() else None
    if base:
        candidata = Path(base) / p
        if candidata.is_file():
            return candidata
    return p if p.is_file() else None


def cargar(ruta: str, base: Optional[Path] = None,
           ancho_max: int = ANCHO_MAX, alto_max: int = ALTO_MAX) -> Dict[str, Any]:
    """Devuelve {'imagen': PhotoImage, 'ancho', 'alto', 'original', 'ruta'} o
    {'error': ...}.

    Quien reciba 'imagen' debe mantener una referencia mientras esté en
    pantalla, o Tk la borra."""
    archivo = resolver(ruta, base)
    if archivo is None:
        return {"error": f"No encontré la imagen: {ruta}"}

    try:
        tamano = archivo.stat().st_size
    except OSError as e:
        return {"error": f"No pude leer {archivo.name}: {e}"}
    if tamano > MAX_BYTES:
        return {"error": f"{archivo.name} pesa {tamano/1024/1024:.0f} MB; "
                         f"demasiado para mostrar en línea"}

    extension = archivo.suffix.lower()
    if extension not in extensiones_soportadas():
        falta = " (instalá Pillow para más formatos)" if not _hay_pillow() else ""
        return {"error": f"No puedo mostrar archivos {extension}{falta}"}

    if _hay_pillow():
        return _cargar_pillow(archivo, ancho_max, alto_max)
    return _cargar_tk(archivo, ancho_max, alto_max)


def _cargar_pillow(archivo: Path, ancho_max: int, alto_max: int) -> Dict[str, Any]:
    from PIL import Image, ImageTk
    try:
        img = Image.open(archivo)
        original = img.size
        # thumbnail respeta la proporción y solo achica, nunca agranda.
        img.thumbnail((ancho_max, alto_max), Image.LANCZOS)
        foto = ImageTk.PhotoImage(img)
    except Exception as e:
        return {"error": f"No pude abrir {archivo.name}: {e}"}
    return {"imagen": foto, "ancho": foto.width(), "alto": foto.height(),
            "original": original, "ruta": archivo}


def _cargar_tk(archivo: Path, ancho_max: int, alto_max: int) -> Dict[str, Any]:
    import tkinter as tk
    try:
        foto = tk.PhotoImage(file=str(archivo))
    except Exception as e:
        return {"error": f"No pude abrir {archivo.name}: {e}"}

    original = (foto.width(), foto.height())
    # subsample solo divide por enteros: se elige el menor factor que entre.
    factor = max(math.ceil(original[0] / ancho_max),
                 math.ceil(original[1] / alto_max), 1)
    if factor > 1:
        foto = foto.subsample(factor, factor)
    return {"imagen": foto, "ancho": foto.width(), "alto": foto.height(),
            "original": original, "ruta": archivo}
