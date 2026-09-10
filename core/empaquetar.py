"""
Arma el .zip portable para compartir la app.

Lo importante de este módulo no es comprimir, es lo que DEJA AFUERA. Compartir
una app que guarda API keys es la forma más fácil de regalarle tus credenciales
a alguien, así que acá hay dos barreras:

  1. Una lista de exclusión (.env, venv/, datos, __pycache__, .git…).
  2. Una revisión del contenido ya armado, buscando algo que parezca una clave.
     Si aparece, el zip NO se entrega. Es una red de seguridad por si la lista
     de exclusión se queda corta ante un archivo nuevo.

Lo que se comparte es solo el código: quien lo reciba arranca con sus propios
datos vacíos y sus propias claves.
"""

import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from core import plataforma

# Nada de esto entra al zip.
EXCLUIR_NOMBRES = {
    ".env", ".env.local", ".DS_Store", "__pycache__", ".git", ".idea",
    "venv", ".venv", "env", "runtime", "datos", "_versiones", "_conversaciones",
    "_biblioteca", "_papelera", "node_modules",
    # Carpetas de trabajo que aparecen cuando la app corrió en esta máquina.
    "_logs", "_proxy", "_tareas", "_paquetes", ".pytest_cache", ".mypy_cache",
    # Salida de PyInstaller. Además de pesar cientos de MB, trae adentro el
    # código de las dependencias, y el revisor de secretos encuentra ahí las
    # cadenas de ejemplo de mitmproxy y se niega —con razón— a generar el zip.
    "dist", "build", "build_pyi",
}
EXCLUIR_SUFIJOS = {".pyc", ".pyo", ".bak", ".log", ".sqlite", ".key", ".pem"}

# Restos de instalaciones viejas que no aportan nada al paquete portable.
EXCLUIR_ARCHIVOS = {"main.py", "iniciar_interactivo.sh", ".icon",
                    # README.md ya NO se excluye: pasó a ser la guía de
                    # instalación de las dos plataformas, así que es justamente
                    # lo que hay que llevar.
                    # crear_zip() escribe su propio LEEME.md al final; sin
                    # excluir el del repo, el zip queda con la entrada duplicada
                    # y el que gana al descomprimir depende de la herramienta.
                    "LEEME.md",
                    # Sello de "ya instalé las dependencias". Si viaja en el
                    # zip, la máquina que lo recibe cree que ya tiene todo
                    # puesto, saltea pip y muere en la verificación.
                    ".requisitos-instalados"}

# Sin estos la app no arranca. Se verifica ANTES de entregar el zip, porque un
# paquete al que le falta un archivo falla recién en la máquina del otro — que
# es exactamente lo que pasó con main_ui.py.
#
# La lista se partió en común + por sistema. Antes exigía los archivos del
# bundle .app de macOS, que no existen en este árbol: `crear_zip()` devolvía
# "al paquete le faltan archivos" SIEMPRE, en cualquier sistema. La ruta del
# zip portable estaba rota de entrada.
REQUERIDOS_COMUNES = ["bootstrap.py", "main_ui.py", "requirements.txt",
                      "core/chat.py", "core/rutas.py", "core/plataforma.py"]

# Los arrancadores de cada sistema. Se piden los dos: un paquete tiene que
# poder abrirse tanto en la Mac como en Windows, que es el punto de que la app
# sea portable.
REQUERIDOS_ARRANQUE = ["INICIAR.command", "INICIAR.bat"]

ARCHIVOS_REQUERIDOS = REQUERIDOS_COMUNES + REQUERIDOS_ARRANQUE

# Formas típicas de credenciales. Se buscan en el contenido de lo que se va a
# comprimir, no en los nombres.
PATRONES_SECRETO = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "API key de Anthropic"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API key tipo OpenAI/DeepSeek"),
    (re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\s*=\s*['\"]?[A-Za-z0-9_\-]{16,}"),
     "credencial asignada en el código"),
]

# Archivos que se leen para revisar. Los binarios no se inspeccionan.
SUFIJOS_TEXTO = {".py", ".sh", ".command", ".bat", ".txt", ".md", ".json",
                 ".cfg", ".ini", ".toml", ".yml", ".yaml", ""}


def _se_excluye(ruta: Path, raiz: Path) -> bool:
    relativa = ruta.relative_to(raiz)
    if any(parte in EXCLUIR_NOMBRES for parte in relativa.parts):
        return True
    if relativa.as_posix() in EXCLUIR_ARCHIVOS:
        return True
    return ruta.suffix.lower() in EXCLUIR_SUFIJOS


def revisar_completitud(archivos: List[Path], raiz: Path) -> List[str]:
    """Qué falta para que el paquete arranque del otro lado.

    Dos controles: los archivos de arranque, y que cada 'from core.X import'
    tenga su módulo adentro. El segundo es el que evita que esto se repita
    cuando agreguemos un módulo nuevo y me olvide de la lista."""
    # as_posix() y no str(): en Windows str(Path('core/chat.py')) da
    # 'core\\chat.py' y no coincidiría nunca con la lista de requeridos, que
    # está escrita con '/'. Sin esto, el control da que faltan TODOS los
    # módulos de core y el zip no se genera jamás en Windows.
    presentes = {a.relative_to(raiz).as_posix() for a in archivos}
    faltan = [r for r in ARCHIVOS_REQUERIDOS if r not in presentes]

    importados = set()
    for archivo in archivos:
        if archivo.suffix != ".py":
            continue
        try:
            contenido = archivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"^\s*(?:from|import)\s+core\.(\w+)", contenido, re.M):
            importados.add(f"core/{m.group(1)}.py")

    faltan += sorted(m for m in importados if m not in presentes)
    return sorted(set(faltan))


def archivos_a_incluir(raiz: Path) -> List[Path]:
    raiz = Path(raiz)
    return sorted(p for p in raiz.rglob("*")
                  if p.is_file() and not _se_excluye(p, raiz))


def revisar_secretos(archivos: List[Path], raiz: Path) -> List[Dict[str, str]]:
    """Busca credenciales en lo que está por comprimirse."""
    hallazgos: List[Dict[str, str]] = []
    for archivo in archivos:
        if archivo.suffix.lower() not in SUFIJOS_TEXTO:
            continue
        try:
            contenido = archivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for patron, descripcion in PATRONES_SECRETO:
            m = patron.search(contenido)
            if m:
                # Nunca se registra el valor encontrado, solo dónde está.
                hallazgos.append({
                    "archivo": str(archivo.relative_to(raiz)),
                    "que": descripcion,
                    "linea": str(contenido[:m.start()].count("\n") + 1),
                })
                break
    return hallazgos


def crear_zip(destino: Path, raiz: Path = None, forzar: bool = False) -> Dict[str, Any]:
    """Arma el zip portable. Si detecta credenciales, no lo crea.

    `forzar` saltea la revisión: existe solo para casos donde el hallazgo sea
    un falso positivo confirmado por vos, nunca por defecto.
    """
    from core.rutas import dir_app

    raiz = Path(raiz) if raiz else dir_app()
    destino = Path(destino)

    archivos = archivos_a_incluir(raiz)
    if not archivos:
        return {"error": f"No encontré archivos para empaquetar en {raiz}"}

    incompleto = revisar_completitud(archivos, raiz)
    if incompleto:
        return {
            "error": "Al paquete le faltan archivos: no arrancaría en otra máquina.",
            "faltan": incompleto,
        }

    hallazgos = revisar_secretos(archivos, raiz)
    if hallazgos and not forzar:
        return {
            "error": "Encontré algo que parece una credencial. No genero el zip.",
            "hallazgos": hallazgos,
        }

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for archivo in archivos:
                interno = Path(raiz.name) / archivo.relative_to(raiz)
                # as_posix() obligatorio: el formato ZIP especifica '/' como
                # separador. Armado en Windows con str() quedarían entradas con
                # '\', y al descomprimir en macOS o Linux saldrían archivos
                # llamados literalmente "AgentFactory\core\chat.py", todos
                # sueltos en la raíz.
                info = zipfile.ZipInfo(interno.as_posix())
                # zipfile NO preserva permisos: sin esto, INICIAR.command sale
                # del zip sin bit de ejecución y el doble clic no hace nada.
                modo = archivo.stat().st_mode
                if archivo.suffix in (".command", ".sh") or os.access(archivo, os.X_OK):
                    modo |= 0o755
                info.external_attr = (modo & 0xFFFF) << 16
                info.date_time = time.localtime(archivo.stat().st_mtime)[:6]
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, archivo.read_bytes())
            z.writestr(f"{raiz.name}/LEEME.md", LEEME)
    except OSError as e:
        return {"error": f"No pude escribir {destino}: {e}"}

    return {
        "zip": str(destino),
        "archivos": len(archivos),
        "bytes": destino.stat().st_size,
        "revisado": True,
        "hallazgos": hallazgos if forzar else [],
    }


LANZADOR = """#!/bin/bash
# Lanzador del .app. El código viaja adentro del bundle, en Resources/app, y se
# despliega a ~/AgenteDeepSeek la primera vez (o cuando la app trae una versión
# más nueva). Se despliega en vez de correr adentro del bundle para que el
# runtime de Python, el entorno y el historial de versiones NO queden dentro de
# la app: así reemplazarla no te borra los 24 MB del intérprete ni tus puntos
# de retorno.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
ORIGEN="$RES/app"
DESTINO="$HOME/AgenteDeepSeek"

version_origen="$(cat "$ORIGEN/VERSION" 2>/dev/null)"
version_destino="$(cat "$DESTINO/VERSION" 2>/dev/null)"

if [ ! -f "$DESTINO/INICIAR.command" ] || [ "$version_origen" != "$version_destino" ]; then
    mkdir -p "$DESTINO"
    # ditto respeta permisos: sin eso INICIAR.command queda sin bit de ejecución.
    /usr/bin/ditto "$ORIGEN" "$DESTINO"
fi

# ¿Hace falta preparar el entorno? Si sí, se abre una Terminal para que se vea
# el progreso de la descarga; si no, la app abre directo y sin ventanas de más.
listo=0
for py in "$DESTINO/venv/bin/python3" "$DESTINO/runtime/python/bin/python3"; do
    if "$py" -c 'import tkinter, openai' >/dev/null 2>&1; then listo=1; break; fi
done

if [ "$listo" = "1" ]; then
    exec "$DESTINO/INICIAR.command" "$@"
fi
open -a Terminal "$DESTINO/INICIAR.command"
"""

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>AgenteDeepSeek</string>
    <key>CFBundleDisplayName</key><string>AgenteDeepSeek</string>
    <key>CFBundleIdentifier</key><string>local.agentedeepseek</string>
    <key>CFBundleVersion</key><string>{version}</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>AgenteDeepSeek</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def construir_app(destino: Path, raiz: Path = None, icono: Path = None) -> Dict[str, Any]:
    """Arma un .app autocontenido con el código adentro.

    A diferencia del zip, acá el otro recibe UN icono y no una carpeta con
    archivos sueltos. El contenido es el mismo y pasa por los mismos controles
    de completitud y de credenciales.
    """
    if not plataforma.ES_MAC:
        return {"error": "El .app y el .dmg se arman con ditto y hdiutil, que solo existen en macOS. En Windows usa el paquete portable (.zip)."}
    import shutil
    import subprocess
    from core.rutas import dir_app

    raiz = Path(raiz) if raiz else dir_app()
    destino = Path(destino)

    archivos = archivos_a_incluir(raiz)
    incompleto = revisar_completitud(archivos, raiz)
    if incompleto:
        return {"error": "Al paquete le faltan archivos: no arrancaría en otra máquina.",
                "faltan": incompleto}
    hallazgos = revisar_secretos(archivos, raiz)
    if hallazgos:
        return {"error": "Encontré algo que parece una credencial. No genero la app.",
                "hallazgos": hallazgos}

    version = time.strftime("%Y%m%d-%H%M%S")
    contenido = destino / "Contents"
    if destino.exists():
        shutil.rmtree(destino)
    (contenido / "MacOS").mkdir(parents=True)
    (contenido / "Resources" / "app").mkdir(parents=True)

    for archivo in archivos:
        relativa = archivo.relative_to(raiz)
        # El .app anterior no se mete adentro del nuevo.
        if str(relativa).startswith("AgenteDeepSeek.app/"):
            continue
        salida = contenido / "Resources" / "app" / relativa
        salida.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archivo, salida)

    (contenido / "Resources" / "app" / "VERSION").write_text(version, encoding="utf-8")
    (contenido / "Resources" / "app" / "LEEME.md").write_text(LEEME, encoding="utf-8")

    icono = Path(icono) if icono else (raiz / "AgenteDeepSeek.app" / "Contents" /
                                       "Resources" / "AppIcon.icns")
    if icono.exists():
        shutil.copy2(icono, contenido / "Resources" / "AppIcon.icns")

    lanzador = contenido / "MacOS" / "AgenteDeepSeek"
    lanzador.write_text(LANZADOR, encoding="utf-8")
    lanzador.chmod(0o755)
    (contenido / "Info.plist").write_text(INFO_PLIST.format(version=version), encoding="utf-8")

    total = sum(f.stat().st_size for f in destino.rglob("*") if f.is_file())
    return {"app": str(destino), "version": version,
            "archivos": len(archivos), "bytes": total}


def construir_dmg(destino: Path, app: Path) -> Dict[str, Any]:
    """Empaqueta el .app en un disco de instalación, que es como se distribuye
    una app de Mac: se abre, se arrastra a Aplicaciones y listo."""
    if not plataforma.ES_MAC:
        return {"error": "El .app y el .dmg se arman con ditto y hdiutil, que solo existen en macOS. En Windows usa el paquete portable (.zip)."}
    import shutil
    import subprocess
    import tempfile

    destino, app = Path(destino), Path(app)
    if not app.is_dir():
        return {"error": f"No encuentro la app en {app}"}

    with tempfile.TemporaryDirectory() as tmp:
        escena = Path(tmp) / "AgenteDeepSeek"
        escena.mkdir()
        subprocess.run(["/usr/bin/ditto", str(app), str(escena / app.name)], check=True)
        # El atajo a /Applications es lo que hace obvio el "arrastrá acá".
        (escena / "Aplicaciones").symlink_to("/Applications")
        (escena / "LEEME.md").write_text(LEEME, encoding="utf-8")

        if destino.exists():
            destino.unlink()
        proceso = subprocess.run(
            ["/usr/bin/hdiutil", "create", "-volname", "AgenteDeepSeek",
             "-srcfolder", str(escena), "-ov", "-format", "UDZO", str(destino)],
            capture_output=True, text=True)
        if proceso.returncode != 0:
            return {"error": f"hdiutil falló: {(proceso.stderr or '').strip()[:300]}"}

    return {"dmg": str(destino), "bytes": destino.stat().st_size}


LEEME = """# AgentFactory - portable

Este paquete trae **solo el codigo**. No incluye claves ni conversaciones:
cuando lo abras, vas a configurar tus propias API keys desde la app.

**No hace falta tener Python instalado.** Si esta computadora no tiene uno
usable, el arrancador se baja uno y lo deja adentro de esta misma carpeta. No
instala nada en el sistema ni pide contrasena de administrador: si borras la
carpeta, no queda rastro.

## Windows 10 / 11

1. Descomprimi el zip **entero** en una carpeta tuya
   (por ejemplo `C:\\Users\\<vos>\\AgentFactory`).
2. Doble clic en **INICIAR.bat**.

La primera vez se abre una ventana negra mostrando la preparacion: baja Python
(~30 MB) e instala las dependencias. Tarda un par de minutos y necesita
internet. Cuando termina, la app abre sola y la ventana negra se cierra. Los
arranques siguientes son directos.

Dos avisos esperables la primera vez:

  - **"Windows protegio tu PC"** (SmartScreen). Es porque el archivo no esta
    firmado. Clic en *Mas informacion* -> *Ejecutar de todas formas*.
  - Si descomprimiste con el Explorador y el `.bat` no arranca, hace clic
    derecho sobre el -> *Propiedades* -> marca **Desbloquear** -> Aceptar.

Importante: descomprimi **antes** de ejecutar. Si haces doble clic en el `.bat`
desde adentro del zip, Windows lo corre en una carpeta temporal y la
instalacion se pierde.

El `python.exe` que Windows trae en el PATH **no es Python**: es un atajo que
abre la Microsoft Store. El arrancador lo detecta y no lo usa.

Para ver el detalle de un arranque que falla, corre `INICIAR.bat` desde una
consola, o mira el log en `%USERPROFILE%\\tmp\\agentfactory\\_logs\\ui.log`.

## macOS

1. Descomprimi el zip.
2. Doble clic en **INICIAR.command**.

La primera vez macOS va a decir que no puede verificar al desarrollador: la app
no esta firmada con un Apple Developer ID. Para abrirla:

  - Clic derecho sobre **INICIAR.command** -> **Abrir** -> Abrir.
  - Si macOS no ofrece "Abrir": Ajustes del Sistema -> Privacidad y
    seguridad -> bajar hasta el aviso -> "Abrir igualmente".

O desde la Terminal, una sola vez:

    xattr -dr com.apple.quarantine "<carpeta descomprimida>"

Aviso: `/usr/bin/python3` que trae macOS **no es Python**, es un atajo que abre
el instalador de las herramientas de desarrollo de Apple. El arrancador lo
detecta y no lo usa.

## Requisitos

  - Windows 10 (1803 o posterior) / 11 de 64 bits, o Mac con Apple Silicon
  - Conexion a internet la primera vez

## Tus datos

Las conversaciones, tareas y la biblioteca se guardan **fuera** de esta
carpeta:

    Windows   %USERPROFILE%\\tmp\\agentfactory\\
    macOS     ~/tmp/agentfactory/

Las API keys van en el `.env` de esa carpeta, no en la del codigo, asi que si
volves a compartir el zip no viajan con el. Reemplazar esta carpeta por una
version nueva de la app no toca tus datos.
"""
