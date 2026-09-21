"""
App icon in the Dock and in macOS's native dialogs.

The problem: the launcher execs Python directly, so when Tkinter initializes
NSApplication, macOS takes the icon from Python's bundle (the generic one)
instead of our .app's. Tk's `iconphoto` fixes the window, but the native dialogs
(messagebox) and the Dock read `NSApp.applicationIconImage`, which Tk doesn't
touch.

The solution without adding pyobjc as a dependency: talk to the Objective-C
runtime via ctypes —which on macOS is always present— to do the equivalent of

    NSApp.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(png))

It's best-effort: if something fails (another platform, a weird macOS), it breaks
nothing, it just keeps the generic icon. It must never take down the app over a
cosmetic matter.

(The error strings it returns are still Spanish on purpose: they feed the
Diagnostics panel and move to the i18n layer in a later phase.)
"""

import ctypes
import ctypes.util
import sys
from pathlib import Path
from typing import Optional

_c_void_p = ctypes.c_void_p


def _load_objc():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = _c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = _c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    # objc_msgSend has no single signature; it's re-cast per call according to
    # the argument types. That's why each use defines argtypes/restype first.
    return objc


def set_dock_icon(png: Path) -> Optional[str]:
    """Sets the app icon from a PNG. Returns an error (str) or None.

    Never raises: any problem comes back as a string and the app carries on."""
    if sys.platform != "darwin":
        return "solo aplica en macOS"
    png = Path(png)
    if not png.exists():
        return f"no existe {png}"

    try:
        objc = _load_objc()

        def msg(receiver, selector, restype=_c_void_p, argtypes=(), *args):
            objc.objc_msgSend.restype = restype
            objc.objc_msgSend.argtypes = [_c_void_p, _c_void_p, *argtypes]
            return objc.objc_msgSend(receiver, objc.sel_registerName(selector), *args)

        # NSString* path = [NSString stringWithUTF8String:png]
        NSString = objc.objc_getClass(b"NSString")
        path = msg(NSString, b"stringWithUTF8String:", _c_void_p,
                   (ctypes.c_char_p,), str(png).encode("utf-8"))
        if not path:
            return "no pude crear NSString"

        # NSImage* img = [[NSImage alloc] initWithContentsOfFile:path]
        NSImage = objc.objc_getClass(b"NSImage")
        img = msg(NSImage, b"alloc")
        img = msg(img, b"initWithContentsOfFile:", _c_void_p, (_c_void_p,), path)
        if not img:
            return "NSImage no pudo cargar el PNG"

        # NSApp = [NSApplication sharedApplication]; [NSApp setApplicationIconImage:img]
        NSApplication = objc.objc_getClass(b"NSApplication")
        app = msg(NSApplication, b"sharedApplication")
        if not app:
            return "no hay NSApplication (¿sesión sin GUI?)"
        msg(app, b"setApplicationIconImage:", None, (_c_void_p,), img)
        return None
    except Exception as e:                      # noqa: BLE001 — never break over the icon
        return f"{type(e).__name__}: {e}"
