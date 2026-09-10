#!/usr/bin/env python3
"""
Segunda mitad del arranque portable: entorno virtual, dependencias y lanzamiento.

El arrancador de cada sistema (INICIAR.command en macOS, INICIAR.bat en
Windows) se encarga de que exista un Python usable — eso no se puede hacer
desde Python, porque es justo lo que puede faltar. De ahí en más manda este
archivo, que es más cómodo de leer y mantener que más bash (o más batch).

Qué hace, en orden:
  1. Crea el venv en <app>/venv si no está.
  2. Instala/actualiza las dependencias solo si cambió requirements.txt.
  3. Verifica que la interfaz pueda arrancar (tkinter).
  4. Lanza main_ui.py.

Es idempotente: en el segundo arranque no hace nada y va directo al paso 4.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

# UTF-8 en la salida antes que nada: este archivo imprime emoji y en una
# consola de Windows (cp850/cp1252) un print sin esto tira UnicodeEncodeError
# y el arranque muere sin decir por qué. No se puede importar core.consola
# todavía: el venv puede no existir.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ES_WINDOWS = os.name == "nt"

APP_DIR = Path(__file__).resolve().parent
VENV_DIR = APP_DIR / "venv"
RUNTIME_DIR = APP_DIR / "runtime"

# El intérprete del venv vive en un lugar distinto según el sistema.
if ES_WINDOWS:
    PY_VENV = VENV_DIR / "Scripts" / "python.exe"
else:
    PY_VENV = VENV_DIR / "bin" / "python3"

# Se completa en main(): con runtime propio se instala directo ahí (es nuestro
# y no lo usa nadie más); con un Python del sistema se arma un venv para no
# ensuciárselo a la persona.
PY_APP = None
REQUISITOS = APP_DIR / "requirements.txt"

# Versión mínima. No es 3.9 por capricho: mitmproxy 12 (la captura de tráfico)
# pide 3.12+, así que aceptar un 3.10 del sistema pasa este control y después
# revienta en pip, que es mucho peor lugar para enterarse.
PY_MINIMO = (3, 12)

# En Windows las secuencias ANSI se ven literales en muchas consolas, así que
# no se usan. En macOS/Linux sí.
if ES_WINDOWS:
    VERDE = AMARILLO = ROJO = AZUL = NC = ""
else:
    VERDE, AMARILLO, ROJO, AZUL, NC = (
        "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0;34m", "\033[0m")


def log(mensaje, color=""):
    print(f"{color}{mensaje}{NC}" if color else mensaje, flush=True)


def morir(mensaje, detalle=""):
    log(f"❌ {mensaje}", ROJO)
    if detalle:
        log(f"   {detalle}")
    log("")
    try:
        input("Enter para cerrar… ")
    except (EOFError, KeyboardInterrupt):
        pass
    sys.exit(1)


def hash_requisitos() -> str:
    if not REQUISITOS.exists():
        return ""
    return hashlib.sha256(REQUISITOS.read_bytes()).hexdigest()[:16]


def sello() -> Path:
    """Dónde se anota el hash de requirements.txt con el que se instaló.

    Va ADENTRO del venv (o del runtime propio), nunca en la raíz de la app.
    Es deliberado: esas dos carpetas jamás viajan en un zip, así que una
    instalación nueva siempre instala. Cuando el sello vivía en la raíz, se
    empaquetaba junto al código y la máquina que descomprimía el zip creía que
    ya tenía las dependencias puestas — y moría más adelante, en la
    verificación, con un mensaje que no ayudaba a nadie.
    """
    base = RUNTIME_DIR if usando_runtime_propio() else VENV_DIR
    return base / ".requisitos-instalados"


def usando_runtime_propio() -> bool:
    """¿Estamos corriendo con el Python que bajó el arrancador?"""
    try:
        return Path(sys.executable).resolve().is_relative_to(RUNTIME_DIR.resolve())
    except (AttributeError, ValueError):  # is_relative_to es 3.9+
        return str(RUNTIME_DIR).lower() in str(Path(sys.executable).resolve()).lower()


def venv_sano() -> bool:
    """Un venv puede quedar roto si se movió la carpeta: sus symlinks apuntan
    a rutas absolutas. Mejor detectarlo y rehacerlo que fallar más adelante."""
    if not PY_VENV.exists():
        return False
    try:
        return subprocess.run([str(PY_VENV), "-c", "import sys"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def crear_venv():
    if venv_sano():
        return

    if VENV_DIR.exists():
        log("♻️  El entorno anterior quedó roto (¿se movió la carpeta?). Lo rehago.", AMARILLO)
        import shutil
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    log("🔧 Preparando el entorno (esto pasa una sola vez)…", AZUL)
    # Los builds framework de Apple no soportan --copies ("cannot create venvs
    # without using symlinks"), así que se intenta y se cae a symlinks.
    ultimo = ""
    for extra in (["--copies"], []):
        try:
            subprocess.run([sys.executable, "-m", "venv", *extra, str(VENV_DIR)],
                           check=True, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
            return
        except subprocess.CalledProcessError as e:
            ultimo = (e.stderr or "").strip()
    morir("No pude crear el entorno virtual", ultimo[:400])


def instalar_dependencias():
    esperado = hash_requisitos()
    marca = sello()
    if marca.exists() and marca.read_text(encoding="utf-8").strip() == esperado:
        return  # ya está, y requirements.txt no cambió

    if not REQUISITOS.exists():
        log("⚠️  No encontré requirements.txt; sigo sin instalar nada.", AMARILLO)
        return

    log("📦 Instalando dependencias (necesita internet, tarda un minuto)…", AZUL)
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        subprocess.run([str(PY_APP), "-m", "pip", "install", "--upgrade", "pip"],
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=entorno)
        subprocess.run([str(PY_APP), "-m", "pip", "install", "-r", str(REQUISITOS)],
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=entorno)
    except subprocess.CalledProcessError as e:
        morir("No pude instalar las dependencias",
              (e.stderr or e.stdout or "").strip()[-500:])

    marca.parent.mkdir(parents=True, exist_ok=True)
    marca.write_text(esperado, encoding="utf-8")
    log("✅ Dependencias listas", VERDE)


def verificar():
    """Que la interfaz pueda abrir de verdad, antes de intentar lanzarla."""
    prueba = subprocess.run(
        [str(PY_APP), "-c",
         "import tkinter, openai, dotenv; print(tkinter.TkVersion)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if prueba.returncode != 0:
        morir("El entorno quedó incompleto",
              (prueba.stderr or "").strip()[-400:])


def ruta_datos() -> Path:
    """Dónde van los logs. Se lee del entorno (lo pone el arrancador) en vez de
    importar core.rutas: este proceso puede ser el Python de afuera, que
    todavía no tiene las dependencias del venv."""
    valor = os.getenv("AGENTE_DATOS")
    return Path(valor).expanduser() if valor else APP_DIR


def lanzar(destino: Path):
    """Le pasa el control a la interfaz.

    En POSIX con execv: el proceso de la UI reemplaza a este, así no queda un
    python colgado de más mientras la app está abierta.

    En Windows execv NO reemplaza el proceso — la implementación del CRT lanza
    uno nuevo y mata el actual, así que cmd.exe da por terminado el .bat y le
    corre una carrera a la ventana que está abriendo. Además la consola
    quedaría abierta atrás toda la sesión. Por eso acá se lanza pythonw.exe
    (subsistema GUI, sin consola) desprendido, y este proceso se va enseguida.
    """
    argumentos = sys.argv[1:]

    if not ES_WINDOWS:
        os.execv(str(PY_APP), [str(PY_APP), str(destino), *argumentos])
        return  # inalcanzable

    # Con AGENTE_CONSOLA=1 se queda pegado a la consola y se ve todo lo que
    # imprime la UI. Es la única forma práctica de ver un traceback de arranque
    # cuando el camino normal va desprendido.
    if os.getenv("AGENTE_CONSOLA") == "1":
        resultado = subprocess.run([str(PY_APP), str(destino), *argumentos])
        sys.exit(resultado.returncode)

    pythonw = PY_APP.with_name("pythonw.exe")
    interprete = pythonw if pythonw.exists() else PY_APP

    carpeta_log = ruta_datos() / "_logs"
    try:
        carpeta_log.mkdir(parents=True, exist_ok=True)
        archivo_log = carpeta_log / "ui.log"
        # Sin esto la UI arranca con stdout cerrado y cualquier error de
        # importación se pierde en el aire.
        salida = open(archivo_log, "a", encoding="utf-8", errors="replace")
    except OSError:
        archivo_log, salida = None, subprocess.DEVNULL

    DESPRENDIDO = 0x00000008        # DETACHED_PROCESS
    GRUPO_NUEVO = 0x00000200        # CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [str(interprete), str(destino), *argumentos],
            cwd=str(APP_DIR), stdout=salida, stderr=salida,
            stdin=subprocess.DEVNULL, close_fds=True,
            creationflags=DESPRENDIDO | GRUPO_NUEVO,
            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    except OSError as e:
        morir("No pude abrir la interfaz", str(e))

    if archivo_log:
        log(f"   (si algo falla, el detalle queda en {archivo_log})")
    sys.exit(0)


def main():
    if not (APP_DIR / "main_ui.py").exists():
        morir(f"No encuentro main_ui.py en {APP_DIR}",
              "¿Se descomprimió la carpeta entera?")

    if sys.version_info < PY_MINIMO:
        morir(f"Necesito Python {'.'.join(map(str, PY_MINIMO))} o más nuevo",
              f"Este es {'.'.join(map(str, sys.version_info[:3]))} ({sys.executable})")

    global PY_APP
    if usando_runtime_propio():
        # El runtime lo bajamos nosotros y vive adentro de la carpeta de la
        # app: no hay nada que proteger de nuestras dependencias, y evitamos
        # el problema de los symlinks absolutos de un venv si la carpeta se mueve.
        PY_APP = Path(sys.executable)
    else:
        crear_venv()
        PY_APP = PY_VENV

    instalar_dependencias()
    verificar()

    log("")
    log("🚀 Abriendo…", VERDE)
    lanzar(APP_DIR / "main_ui.py")


if __name__ == "__main__":
    main()
