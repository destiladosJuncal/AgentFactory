"""
Todo lo que cambia entre macOS, Windows y Linux, en un solo lugar.

Existe para que portar a Windows sea completar una tabla y no reescribir la UI
y el módulo de ejecución. Hoy solo macOS está probado de punta a punta; el
resto está implementado pero sin uso real todavía — donde eso importa, está
dicho en el docstring de cada función.

Lo que abstrae:
  · abrir una carpeta en el explorador de archivos
  · mandar algo a la papelera (en vez de borrarlo)
  · qué shell se usa para ejecutar comandos
  · qué fuentes existen
  · qué comandos son destructivos (difieren por completo entre bash y cmd)
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.platform == "darwin":
    SISTEMA = "mac"
elif os.name == "nt":
    SISTEMA = "windows"
else:
    SISTEMA = "linux"

ES_MAC = SISTEMA == "mac"
ES_WINDOWS = SISTEMA == "windows"


# --- Apariencia -------------------------------------------------------------

_FUENTES = {
    "mac":     ("Helvetica Neue", "Menlo"),
    "windows": ("Segoe UI", "Consolas"),
    "linux":   ("DejaVu Sans", "DejaVu Sans Mono"),
}


def fuentes() -> Tuple[str, str]:
    """(fuente de interfaz, fuente monoespaciada) para este sistema."""
    return _FUENTES.get(SISTEMA, _FUENTES["linux"])


# --- Explorador de archivos -------------------------------------------------

def abrir_carpeta(ruta: Path) -> Optional[str]:
    """Abre una carpeta en Finder/Explorador. Devuelve un error si no pudo.

    Antes esto era os.system(f'open "{ruta}"'), que además de ser solo de macOS
    pasaba la ruta por el shell: una carpeta con comillas o punto y coma en el
    nombre podía ejecutar cualquier cosa. Acá va sin shell.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        return f"No existe: {ruta}"
    try:
        if ES_MAC:
            subprocess.run(["open", str(ruta)], check=False)
        elif ES_WINDOWS:
            os.startfile(str(ruta))  # noqa: S606 - la API de Windows para esto
        else:
            subprocess.run(["xdg-open", str(ruta)], check=False)
        return None
    except Exception as e:
        return f"No pude abrir {ruta}: {e}"


def abrir(destino) -> Optional[str]:
    """Abre lo que sea: un archivo con su app por defecto, una carpeta en el
    explorador, o una URL en el navegador.

    Es lo mismo que hacés vos en la terminal con `open <cosa>`, pero sin pasar
    por el shell — una ruta con comillas o punto y coma en el nombre no puede
    ejecutar nada."""
    destino = str(destino).strip()
    if not destino:
        return "No hay nada que abrir"

    es_url = destino.startswith(("http://", "https://"))
    if not es_url:
        ruta = Path(limpiar_ruta(destino))
        if not ruta.exists():
            return f"No existe: {ruta}"
        destino = str(ruta)

    try:
        if ES_MAC:
            subprocess.run(["open", destino], check=False)
        elif ES_WINDOWS:
            os.startfile(destino)  # noqa: S606
        else:
            subprocess.run(["xdg-open", destino], check=False)
        return None
    except Exception as e:
        return f"No pude abrir {destino}: {e}"


def limpiar_ruta(texto: str) -> str:
    """Normaliza lo que puede venir escrito como ruta.

    Los modelos suelen devolver 'file:///Users/...' porque es lo que ven en un
    navegador. Eso no es una ruta: hay que sacarle el esquema y deshacer el
    percent-encoding ('%20' -> espacio)."""
    from urllib.parse import unquote, urlparse
    texto = str(texto).strip().strip("<>\"'")
    if texto.startswith("file://"):
        texto = unquote(urlparse(texto).path)
    elif "%" in texto:
        texto = unquote(texto)
    return str(Path(texto).expanduser())


# --- Papelera ---------------------------------------------------------------

def carpeta_papelera() -> Path:
    """Dónde van las cosas borradas.

    En macOS es la Papelera real, recuperable desde Finder. En Windows y Linux
    no hay una ruta estándar a la que se pueda mover a mano sin bibliotecas
    extra, así que se usa una papelera propia adentro de los datos: se recupera
    igual, solo que a mano.
    """
    if ES_MAC:
        return Path.home() / ".Trash"
    from core.rutas import dir_datos
    return dir_datos() / "_papelera"


def mover_a_papelera(ruta: Path) -> Path:
    """Manda una carpeta o archivo a la papelera en vez de borrarlo."""
    ruta = Path(ruta)
    papelera = carpeta_papelera()
    papelera.mkdir(parents=True, exist_ok=True)

    destino = papelera / ruta.name
    i = 2
    while destino.exists():
        destino = papelera / f"{ruta.name}-{i}"
        i += 1

    shutil.move(str(ruta), str(destino))
    return destino


# --- Ejecución --------------------------------------------------------------

def shell_por_defecto() -> List[str]:
    """Prefijo de comando para correr una línea de shell."""
    if ES_WINDOWS:
        return ["cmd", "/c"]
    return [os.environ.get("SHELL", "/bin/bash"), "-c"]


# Operaciones destructivas por sistema. Las de Unix y las de Windows no se
# parecen en nada, y una whitelist pensada para bash no protege absolutamente
# nada corriendo en cmd o PowerShell.
PATRONES_DESTRUCTIVOS_WINDOWS: List[Tuple[str, str, str]] = [
    ("del",        r"(?:^|[&|]|\s)\s*del\b",                  "borra archivos (del)"),
    ("erase",      r"(?:^|[&|]|\s)\s*erase\b",                "borra archivos (erase)"),
    ("rd",         r"(?:^|[&|]|\s)\s*(?:rd|rmdir)\b",         "borra directorios (rd/rmdir)"),
    ("ps-remove",  r"\bRemove-Item\b",                        "borra archivos (Remove-Item)"),
    ("ps-clear",   r"\bClear-Content\b",                      "vacía archivos (Clear-Content)"),
    ("move",       r"(?:^|[&|]|\s)\s*move\b",                 "mueve archivos (puede pisar el destino)"),
    ("format",     r"(?:^|[&|]|\s)\s*format\b",               "formatea un volumen"),
    ("diskpart",   r"\bdiskpart\b",                           "opera sobre particiones"),
    ("redirect",   r"(?<![>\d])>(?!>)\s*[^\s|&;]+",           "sobrescribe un archivo con '>'"),
]


def patrones_destructivos_del_sistema() -> List[Tuple[str, str, str]]:
    """Se suman a los de core/ejecucion.py cuando corresponde."""
    return PATRONES_DESTRUCTIVOS_WINDOWS if ES_WINDOWS else []


# --- Permisos ---------------------------------------------------------------

def soporta_permisos_posix() -> bool:
    """En Windows, chmod(0o600) no protege el archivo: los permisos van por
    ACLs. Importa porque el .env guarda las API keys y el diagnóstico tiene que
    decir la verdad sobre si están protegidas o no."""
    return not ES_WINDOWS


def descripcion() -> str:
    nombres = {"mac": "macOS", "windows": "Windows", "linux": "Linux"}
    return f"{nombres.get(SISTEMA, SISTEMA)} · Python {'.'.join(map(str, sys.version_info[:3]))}"
