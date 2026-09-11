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
ES_LINUX = SISTEMA == "linux"


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
    # robocopy /MIR es EL clásico de pérdida de datos en Windows: "espejar"
    # significa BORRAR en el destino todo lo que no esté en el origen. Ningún
    # otro patrón de esta lista lo agarra, porque el comando se llama "copy".
    ("robocopy-mir", r"\brobocopy\b.*?/(?:MIR|PURGE)\b",      "espeja carpetas: BORRA en el destino lo que no esté en el origen"),
    ("reg-delete", r"\breg\s+delete\b|\bRemove-ItemProperty\b|\bRemove-Item\b[^\n]*\bHK(?:LM|CU|CR|U|CC):",
                                                              "borra claves del registro (no hay papelera)"),
    ("permisos",   r"\btakeown\b|\bicacls\b[^\n]*/reset\b",   "cambia dueño o permisos de forma irreversible"),
    ("disco",      r"\bClear-Disk\b|\bFormat-Volume\b|\bRemove-Partition\b|\bInitialize-Disk\b",
                                                              "opera sobre discos o particiones"),
    ("ps-write",   r"\b(?:Set-Content|Out-File)\b(?![^\n]*-Append)",
                                                              "sobrescribe un archivo (Set-Content/Out-File)"),
    ("wipe",       r"\bsdelete\b|\bcipher\b[^\n]*/w|\bfsutil\b[^\n]*setzerodata",
                                                              "borra de forma irrecuperable"),
    ("apagar",     r"(?:^|[&|]|\s)\s*shutdown\b|\bRestart-Computer\b|\bStop-Computer\b",
                                                              "apaga o reinicia la máquina"),
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


def soporta_elevacion() -> bool:
    """¿Se puede pedir 'corré esto como administrador'?

    Hoy solo macOS, que tiene un diálogo del sistema (osascript) al que se le
    delega la contraseña. En Windows haría falta una vuelta por UAC con
    captura de salida, que es un proyecto en sí mismo. Mientras no exista, más
    vale no contárselo al modelo: solo gastaría tokens pidiendo algo que
    siempre le va a ser rechazado."""
    return ES_MAC


# --- Codificación -----------------------------------------------------------

def codificacion_consola() -> str:
    """Con qué codificación leer la salida de un programa del sistema.

    En Windows la consola escribe en la codepage OEM (acá cp850), que NO es la
    misma que la ANSI (cp1252) que usa `text=True` por defecto. Leer `dir` de
    una carpeta llamada 'ñoño' con la codificación equivocada devuelve basura,
    y con algunos bytes directamente levanta UnicodeDecodeError. 'oem' es un
    códec de Python 3.6+ que resuelve a la codepage OEM viva.

    Ojo: esto es para programas DEL SISTEMA. Cuando el hijo es un Python
    nuestro, se le impone UTF-8 por PYTHONIOENCODING y se lee como UTF-8."""
    return "oem" if ES_WINDOWS else "utf-8"


# --- Comandos ---------------------------------------------------------------

# Whitelist de solo lectura del modo iterativo, por sistema.
#
# En Windows la lista de Unix no sirve para nada: `ls`, `cat`, `grep`, `head`
# y `tail` no existen, y `find` existe pero es OTRO comando (busca texto, no
# archivos) — el modelo escribiría `find . -name "*.py"` y recibiría basura,
# que es peor que un error.
#
# Además, el ejecutor corre con shell=False, así que los builtins de cmd (dir,
# type) son inalcanzables: CreateProcess solo encuentra .exe reales. Por eso
# la lista de Windows es corta y honesta. Para lo demás el modelo ya tiene
# herramientas portables: listar_directorio, leer_archivo y buscar_archivos.
_SOLO_LECTURA = {
    "mac":     {"ls", "cat", "find", "grep", "wc", "head", "tail", "tree"},
    "linux":   {"ls", "cat", "find", "grep", "wc", "head", "tail", "tree"},
    "windows": {"where", "findstr", "fc"},
}


def comandos_solo_lectura() -> set:
    return set(_SOLO_LECTURA.get(SISTEMA, _SOLO_LECTURA["linux"]))


def ejemplo_ruta_absoluta() -> str:
    """Una ruta absoluta de ejemplo, para los mensajes de error. Mostrarle
    '/Users/<usuario>/Downloads' a alguien en Windows solo confunde."""
    if ES_WINDOWS:
        return r"C:\Users\<usuario>\Downloads"
    if ES_MAC:
        return "/Users/<usuario>/Downloads"
    return "/home/<usuario>/Downloads"


def descripcion_shell() -> str:
    """Cómo se le describe la herramienta de shell al modelo."""
    if ES_WINDOWS:
        return ("Ejecuta un comando REAL en la máquina, con cmd.exe. Las rutas "
                "usan '\\' y letra de unidad (C:\\Users\\...). NO existen ls, cat, "
                "grep, sed ni awk: son dir, type y findstr. Para PowerShell, "
                "invocalo explícitamente: powershell -NoProfile -Command \"...\". "
                "Para cualquier cosa no trivial preferí ejecutar_python, que se "
                "comporta igual en todos los sistemas.")
    return ("Ejecuta un comando de shell REAL (bash) en la máquina: pipes, "
            "redirecciones y cualquier binario instalado (git, curl, sed, awk, brew).")


def sintaxis_shell() -> str:
    """Para la descripción del parámetro 'comando'."""
    return "con la sintaxis de cmd.exe de Windows" if ES_WINDOWS else "con la sintaxis de bash"


def instrucciones_shell() -> str:
    """El párrafo del system prompt que describe la ejecución.

    Va en CADA mensaje, así que se mantiene del mismo tamaño que el de macOS."""
    if ES_WINDOWS:
        return ("Tenés ejecución real en la máquina de la persona: 'ejecutar_shell' "
                "(cmd.exe de Windows: rutas con '\\' y letra de unidad; dir/type/findstr "
                "en vez de ls/cat/grep; para PowerShell usá "
                "powershell -NoProfile -Command \"...\") y 'ejecutar_python', que es "
                "el camino preferido para cualquier cosa no trivial. Podés trabajar "
                "sobre cualquier ruta del sistema, no solo el workspace. Todo lo que "
                "BORRE o sobrescriba archivos se le pregunta a la persona antes de "
                "correr; no esquives esas operaciones, pedilas normalmente. Si te "
                "rechaza una, no insistas ni busques una forma indirecta de hacer lo "
                "mismo: avisale y ofrecé una alternativa que no borre nada.")
    return ("Tenés ejecución real en la máquina de la persona: 'ejecutar_shell' (bash "
            "completo, con pipes, redirecciones y cualquier binario instalado) y "
            "'ejecutar_python'. Podés trabajar sobre cualquier ruta del sistema, no solo "
            "el workspace. Todo lo que BORRE o sobrescriba archivos se le pregunta a la "
            "persona antes de correr; no esquives esas operaciones, pedilas normalmente. "
            "Si te rechaza una, no insistas ni busques una forma indirecta de hacer lo "
            "mismo: avisale y ofrecé una alternativa que no borre nada.")


def instrucciones_admin() -> str:
    """El párrafo de privilegios. Vacío donde no hay elevación: contarle al
    modelo de una capacidad que no tiene solo produce pedidos rechazados."""
    if not soporta_elevacion():
        return ""
    return ("Podés pedir privilegios de administrador con como_admin=true en "
            "'ejecutar_shell', pero es el último recurso: primero intentá sin "
            "privilegios y solo elevá si el comando REALMENTE los necesita (escribir "
            "en /usr/local, /Library, instalar paquetes del sistema). Explicale "
            "SIEMPRE por qué hace falta antes de pedirlo. Si está deshabilitado, no "
            "insistas: proponé otro camino.\n\n")


# --- Firefox ----------------------------------------------------------------

def ruta_firefox() -> Optional[Path]:
    """Dónde está Firefox, o None si no aparece.

    En macOS es un bundle en una ruta fija. En Windows hay que buscarlo: el
    registro es la fuente confiable (App Paths lo publica exactamente), y las
    rutas típicas quedan de respaldo por si la instalación es portable."""
    forzada = os.environ.get("AGENTE_FIREFOX")
    if forzada and Path(forzada).exists():
        return Path(forzada)

    if ES_MAC:
        bundle = Path("/Applications/Firefox.app")
        return bundle if bundle.exists() else None

    if not ES_WINDOWS:
        desde_path = shutil.which("firefox")
        return Path(desde_path) if desde_path else None

    try:
        import winreg
    except ImportError:
        winreg = None

    if winreg is not None:
        # App Paths: lo que usa el propio Windows para resolver "firefox".
        for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                        raiz,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe") as k:
                    valor, _ = winreg.QueryValueEx(k, None)
                    if valor and Path(valor).exists():
                        return Path(valor)
            except OSError:
                pass

        # Clave propia de Mozilla, incluida la vista de 32 bits.
        for sub in (r"SOFTWARE\Mozilla\Mozilla Firefox",
                    r"SOFTWARE\WOW6432Node\Mozilla\Mozilla Firefox"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:
                    version, _ = winreg.QueryValueEx(k, "CurrentVersion")
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    f"{sub}\\{version}\\Main") as k:
                    valor, _ = winreg.QueryValueEx(k, "PathToExe")
                    if valor and Path(valor).exists():
                        return Path(valor)
            except OSError:
                pass

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        cand = Path(base) / "Mozilla Firefox" / "firefox.exe"
        if cand.exists():
            return cand

    desde_path = shutil.which("firefox")
    return Path(desde_path) if desde_path else None


def como_instalar_firefox() -> str:
    if ES_MAC:
        return "No encontré Firefox en /Applications. Instalalo desde firefox.com."
    return ("No encontré Firefox instalado. Instalalo desde firefox.com, o "
            "apuntá la variable AGENTE_FIREFOX al firefox.exe si lo tenés "
            "en una ubicación no estándar.")


# --- Pantalla ---------------------------------------------------------------

def preparar_dpi():
    """Le avisa a Windows que la app dibuja a la resolución real.

    Sin esto, Tk se declara "no consciente" del DPI y Windows escala la ventana
    entera como un bitmap: en una pantalla al 150% (lo normal en cualquier
    laptop) se ve todo borroso. Devuelve el factor de escala que después hay
    que aplicarle a Tk, porque si no se compensa queda todo diminuto.

    Best-effort y nunca levanta: si algo falla, la app abre igual."""
    if not ES_WINDOWS or os.environ.get("AGENTE_DPI") == "0":
        return None
    try:
        import ctypes
    except ImportError:
        return None
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return None
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 72.0
    except Exception:
        return None


def descripcion() -> str:
    nombres = {"mac": "macOS", "windows": "Windows", "linux": "Linux"}
    return f"{nombres.get(SISTEMA, SISTEMA)} · Python {'.'.join(map(str, sys.version_info[:3]))}"
