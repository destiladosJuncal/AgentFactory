"""
Qué Python usar cuando el agente necesita correr Python.

Parece trivial —`sys.executable`— y no lo es en dos situaciones:

  · **Runtime propio.** Cuando el arrancador bajó su propio Python, no hay
    venv: buscar `venv/bin/python3` (que es lo que hacía core/programador.py)
    no encuentra nada y se cae a un fallback silencioso.

  · **Empaquetado con PyInstaller.** Ahí `sys.executable` es el .exe de la
    app, NO un intérprete. Cualquier `subprocess.run([sys.executable, script])`
    relanza la aplicación entera en vez de correr el script. Hay cuatro
    lugares en el código que hacen exactamente eso (core/ejecucion.py,
    core/paquetes.py, core/biblioteca.py y el programador), así que congelar
    la app sin resolver esto la rompe de formas difíciles de diagnosticar.

Este módulo es el único lugar que decide, para que el día que se compile un
.exe haya que arreglar uno y no cuatro.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from core import plataforma


def _congelado() -> bool:
    """¿Estamos adentro de un bundle de PyInstaller?"""
    return getattr(sys, "frozen", False)


def _junto_al_ejecutable() -> Optional[Path]:
    """El intérprete que se distribuye al lado del .exe, si se lo empaquetó."""
    if not _congelado():
        return None
    base = Path(sys.executable).parent
    nombre = "python.exe" if plataforma.ES_WINDOWS else "python3"
    for cand in (base / nombre, base / "runtime" / "python" / nombre,
                 base / "_internal" / nombre):
        if cand.exists():
            return cand
    return None


def _es_stub_de_la_store(ruta: str) -> bool:
    """El python.exe que Windows deja en el PATH por defecto NO es Python: es
    un atajo que abre la Microsoft Store. Ejecutarlo no corre nada y le abre
    una tienda al usuario, así que nunca puede ser la respuesta."""
    return plataforma.ES_WINDOWS and "windowsapps" in str(ruta).lower()


def _sirve(ruta: str) -> bool:
    """Se comprueba ejecutándolo, no mirando si el archivo existe — que es la
    única forma de distinguir un intérprete real de un atajo."""
    if not ruta or _es_stub_de_la_store(ruta):
        return False
    import subprocess
    try:
        return subprocess.run([ruta, "-c", "import sys"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Nombre inventado y deliberadamente descriptivo: si la app congelada no
# encuentra intérprete, subprocess falla con un FileNotFoundError que NOMBRA el
# problema, en vez de ejecutar algo incorrecto en silencio.
SIN_INTERPRETE = "python-no-encontrado-junto-a-la-app"


def interprete() -> str:
    """El Python con el que correr scripts y módulos del agente.

    Es el mismo que está corriendo la app, salvo que la app esté congelada:
    ahí sys.executable no es un intérprete y hay que buscar el que viaje al
    lado."""
    if not _congelado():
        return sys.executable

    aparte = _junto_al_ejecutable()
    if aparte:
        return str(aparte)

    # Sin intérprete al lado se prueba el del sistema, salteando el atajo de
    # la Store. Devolver sys.executable sería peor: relanzaría la app entera.
    from shutil import which
    for nombre in ("python3", "python"):
        hallado = which(nombre)
        if hallado and _sirve(hallado):
            return hallado
    return SIN_INTERPRETE


def hay_interprete() -> bool:
    """Para que la UI pueda avisar antes de ofrecer algo que no va a andar."""
    return interprete() != SIN_INTERPRETE


def interprete_sin_consola() -> str:
    """Igual que interprete(), pero sin ventana de consola.

    En Windows, pythonw.exe es la variante de subsistema GUI. Importa para las
    tareas programadas: con python.exe, cada corrida le abre a la persona una
    ventana negra que aparece sola y se cierra. Fuera de Windows es lo mismo
    que interprete()."""
    base = Path(interprete())
    if not plataforma.ES_WINDOWS:
        return str(base)
    sin_consola = base.with_name("pythonw.exe")
    return str(sin_consola if sin_consola.exists() else base)


def entorno_utf8(extra: Optional[dict] = None) -> dict:
    """Entorno para un hijo Python nuestro, con la salida en UTF-8.

    Sin PYTHONIOENCODING, un script que imprima '→' con el stdout redirigido a
    un pipe usa la codificación local (cp1252 acá) y muere con
    UnicodeEncodeError. Inofensivo en macOS, imprescindible en Windows."""
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if extra:
        entorno.update(extra)
    return entorno
