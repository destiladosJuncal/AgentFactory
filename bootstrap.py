#!/usr/bin/env python3
"""
Segunda mitad del arranque portable: entorno virtual, dependencias y lanzamiento.

INICIAR.command (bash) se encarga de que exista un Python usable — eso no se
puede hacer desde Python, porque es justo lo que puede faltar. De ahí en más
manda este archivo, que es más cómodo de leer y mantener que más bash.

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

APP_DIR = Path(__file__).resolve().parent
VENV_DIR = APP_DIR / "venv"
PY_VENV = VENV_DIR / "bin" / "python3"

# Se completa en main(): con runtime propio se instala directo ahí (es nuestro
# y no lo usa nadie más); con un Python del sistema se arma un venv para no
# ensuciárselo a la persona.
PY_APP = None
REQUISITOS = APP_DIR / "requirements.txt"
# Guarda el hash de requirements.txt con el que se instaló, para no correr pip
# en cada arranque pero sí cuando el archivo cambie.
SELLO = APP_DIR / ".requisitos-instalados"

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


def usando_runtime_propio() -> bool:
    """¿Estamos corriendo con el Python que bajó INICIAR.command?"""
    try:
        return Path(sys.executable).resolve().is_relative_to((APP_DIR / "runtime").resolve())
    except (AttributeError, ValueError):  # is_relative_to es 3.9+
        return str(APP_DIR / "runtime") in str(Path(sys.executable).resolve())


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
    for extra in (["--copies"], []):
        try:
            subprocess.run([sys.executable, "-m", "venv", *extra, str(VENV_DIR)],
                           check=True, capture_output=True, text=True)
            return
        except subprocess.CalledProcessError as e:
            ultimo = (e.stderr or "").strip()
    morir("No pude crear el entorno virtual", ultimo[:400])


def instalar_dependencias():
    esperado = hash_requisitos()
    if SELLO.exists() and SELLO.read_text().strip() == esperado:
        return  # ya está, y requirements.txt no cambió

    if not REQUISITOS.exists():
        log("⚠️  No encontré requirements.txt; sigo sin instalar nada.", AMARILLO)
        return

    log("📦 Instalando dependencias (necesita internet, tarda un minuto)…", AZUL)
    try:
        subprocess.run([str(PY_APP), "-m", "pip", "install", "--upgrade", "pip"],
                       check=True, capture_output=True, text=True)
        subprocess.run([str(PY_APP), "-m", "pip", "install", "-r", str(REQUISITOS)],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        morir("No pude instalar las dependencias",
              (e.stderr or e.stdout or "").strip()[-500:])

    SELLO.write_text(esperado)
    log("✅ Dependencias listas", VERDE)


def verificar():
    """Que la interfaz pueda abrir de verdad, antes de intentar lanzarla."""
    prueba = subprocess.run(
        [str(PY_APP), "-c",
         "import tkinter, openai, dotenv; print(tkinter.TkVersion)"],
        capture_output=True, text=True)
    if prueba.returncode != 0:
        morir("El entorno quedó incompleto",
              (prueba.stderr or "").strip()[-400:])


def main():
    if not (APP_DIR / "main_ui.py").exists():
        morir(f"No encuentro main_ui.py en {APP_DIR}",
              "¿Se descomprimió la carpeta entera?")

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
    # exec: el proceso de la UI reemplaza a este, así no queda un python
    # colgado de más mientras la app está abierta.
    os.execv(str(PY_APP), [str(PY_APP), str(APP_DIR / "main_ui.py"), *sys.argv[1:]])


if __name__ == "__main__":
    main()
