"""
Ícono de la app en el Dock y en los diálogos nativos de macOS.

El problema: el lanzador hace exec a Python directo, así que cuando Tkinter
inicializa NSApplication, macOS toma el ícono del bundle de Python (el genérico)
en vez del de nuestro .app. `iconphoto` de Tk arregla la ventana, pero los
diálogos nativos (messagebox) y el Dock leen `NSApp.applicationIconImage`, que
Tk no toca.

La solución sin agregar pyobjc como dependencia: hablarle al runtime de
Objective-C por ctypes —que en macOS siempre está— para hacer el equivalente de

    NSApp.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(png))

Es best-effort: si algo falla (otra plataforma, un macOS raro), no rompe nada,
solo se queda con el ícono genérico. Nunca debe tirar la app por un tema
cosmético.
"""

import ctypes
import ctypes.util
import sys
from pathlib import Path
from typing import Optional

_c_void_p = ctypes.c_void_p


def _cargar_objc():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = _c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = _c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    # objc_msgSend no tiene una firma única; se re-castea por llamada según los
    # tipos de argumento. Por eso cada uso define argtypes/restype antes.
    return objc


def poner_icono_dock(png: Path) -> Optional[str]:
    """Setea el ícono de la app desde un PNG. Devuelve un error (str) o None.

    No levanta nunca: cualquier problema vuelve como string y la app sigue."""
    if sys.platform != "darwin":
        return "solo aplica en macOS"
    png = Path(png)
    if not png.exists():
        return f"no existe {png}"

    try:
        objc = _cargar_objc()

        def msg(receptor, selector, restype=_c_void_p, argtypes=(), *args):
            objc.objc_msgSend.restype = restype
            objc.objc_msgSend.argtypes = [_c_void_p, _c_void_p, *argtypes]
            return objc.objc_msgSend(receptor, objc.sel_registerName(selector), *args)

        # NSString* ruta = [NSString stringWithUTF8String:png]
        NSString = objc.objc_getClass(b"NSString")
        ruta = msg(NSString, b"stringWithUTF8String:", _c_void_p,
                   (ctypes.c_char_p,), str(png).encode("utf-8"))
        if not ruta:
            return "no pude crear NSString"

        # NSImage* img = [[NSImage alloc] initWithContentsOfFile:ruta]
        NSImage = objc.objc_getClass(b"NSImage")
        img = msg(NSImage, b"alloc")
        img = msg(img, b"initWithContentsOfFile:", _c_void_p, (_c_void_p,), ruta)
        if not img:
            return "NSImage no pudo cargar el PNG"

        # NSApp = [NSApplication sharedApplication]; [NSApp setApplicationIconImage:img]
        NSApplication = objc.objc_getClass(b"NSApplication")
        app = msg(NSApplication, b"sharedApplication")
        if not app:
            return "no hay NSApplication (¿sesión sin GUI?)"
        msg(app, b"setApplicationIconImage:", None, (_c_void_p,), img)
        return None
    except Exception as e:                      # noqa: BLE001 — jamás romper por el ícono
        return f"{type(e).__name__}: {e}"
