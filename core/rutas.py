"""
Resolución de rutas del agente. Un solo lugar que decide dónde vive cada cosa.

Hay DOS raíces, a propósito:

  DIR_DATOS  ($HOME/tmp/agent_code)   lo tuyo: conversaciones, proyectos,
                                      biblioteca, versiones y el .env con las
                                      claves. Nunca viaja en un zip.

  DIR_APP    (~/AgenteDeepSeek)       los archivos duros: código, venv,
                                      requirements. Esto sí es lo que se
                                      comparte.

Separarlas es lo que hace seguro compartir la app: el .env vive del lado de los
datos, así que un zip del código no puede llevarse tus API keys por accidente.

Ambas se detectan y se crean si no están. El orden de búsqueda permite
sobreescribir con variables de entorno, útil para probar o para correr dos
instalaciones en paralelo:

    AGENTE_DATOS=/otra/carpeta    AGENTE_APP=/otra/instalacion
"""

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

# Nombre histórico de la carpeta de datos. Se mantiene porque ya hay proyectos,
# conversaciones y biblioteca viviendo ahí: cambiarlo obligaría a migrar.
DATOS_POR_DEFECTO = Path.home() / "tmp" / "agent_code"

# Dónde puede estar el código instalado, en orden de preferencia. El primero es
# el que gana en un bundle portable (la carpeta donde está el propio archivo).
CANDIDATOS_APP: List[Path] = [
    Path.home() / "AgenteDeepSeek",
    Path("/Applications/AgenteDeepSeek"),
]

# Subcarpetas de datos que tienen que existir siempre.
SUBCARPETAS = ("_conversaciones", "_biblioteca", "_biblioteca/modulos", "_versiones")

NOMBRE_ENV = ".env"


def _de_entorno(nombre: str) -> Optional[Path]:
    valor = os.getenv(nombre)
    return Path(valor).expanduser() if valor else None


def dir_datos() -> Path:
    """Carpeta de datos. Se crea con toda su estructura si no existe."""
    destino = _de_entorno("AGENTE_DATOS") or _de_entorno("AGENTE_HOME") or DATOS_POR_DEFECTO
    asegurar_estructura(destino)
    return destino


def dir_app() -> Path:
    """Carpeta del código instalado.

    Si este archivo vive adentro de una instalación (lo normal), esa gana: es
    lo que hace que un bundle portable descomprimido en cualquier lado se
    encuentre a sí mismo sin configuración.
    """
    forzada = _de_entorno("AGENTE_APP")
    if forzada:
        return forzada

    # Congelado con PyInstaller no hay archivos .py en disco que reconocer: la
    # instalación es la carpeta del ejecutable.
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    # core/rutas.py -> core/ -> raíz de la instalación
    propia = Path(__file__).resolve().parent.parent
    if (propia / "core" / "chat.py").exists():
        return propia

    for candidata in CANDIDATOS_APP:
        if (candidata / "core" / "chat.py").exists():
            return candidata
    return propia


def asegurar_estructura(base: Optional[Path] = None) -> Dict[str, Any]:
    """Crea la carpeta de datos y sus subcarpetas si faltan.

    Devuelve qué se creó, para poder informarlo en el diagnóstico."""
    base = Path(base) if base else (
        _de_entorno("AGENTE_DATOS") or _de_entorno("AGENTE_HOME") or DATOS_POR_DEFECTO)

    creadas: List[str] = []
    try:
        if not base.exists():
            base.mkdir(parents=True, exist_ok=True)
            creadas.append(str(base))
        for sub in SUBCARPETAS:
            ruta = base / sub
            if not ruta.exists():
                ruta.mkdir(parents=True, exist_ok=True)
                creadas.append(sub)

        registro = base / "_biblioteca" / "registro.json"
        if not registro.exists():
            registro.write_text("[]", encoding="utf-8")
            creadas.append("_biblioteca/registro.json")
    except OSError as e:
        return {"error": f"No pude crear la estructura en {base}: {e}", "creadas": creadas}

    return {"base": str(base), "creadas": creadas}


# --- .env -------------------------------------------------------------------

def ruta_env() -> Path:
    """El .env vive con los DATOS, no con el código.

    Es lo que evita que compartir un zip de la app se lleve tus claves puestas.
    """
    return dir_datos() / NOMBRE_ENV


def migrar_env_si_hace_falta() -> Optional[str]:
    """Trae el .env de la instalación vieja a la carpeta de datos.

    Copia (no mueve) para no romper una instalación en uso, y le pone permisos
    restrictivos. Devuelve un mensaje si hizo algo."""
    destino = ruta_env()
    if destino.exists():
        return None

    for viejo in (dir_app() / NOMBRE_ENV, Path.home() / "AgenteDeepSeek" / NOMBRE_ENV,
                  Path("/Applications/AgenteDeepSeek") / NOMBRE_ENV):
        try:
            if viejo.exists() and viejo.resolve() != destino.resolve():
                shutil.copy2(viejo, destino)
                proteger(destino)
                return f"Configuración migrada desde {viejo} a {destino}"
        except OSError:
            continue

    if not destino.exists():
        destino.write_text(PLANTILLA_ENV, encoding="utf-8")
        proteger(destino)
        return f"Creé una configuración nueva en {destino}"
    return None


def proteger(ruta: Path):
    """Permisos restrictivos para el archivo de claves. En Windows no existe
    chmod POSIX; ahí queda como está y se avisa en el diagnóstico."""
    try:
        ruta.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


PLANTILLA_ENV = """# Configuración del agente. Se edita desde la pestaña ⚙️ Configuración.
# Este archivo vive con tus DATOS, no con el código: un zip para compartir
# nunca se lo lleva.

# Proveedor por defecto: 'deepseek' o 'claude'
AGENTE_PROVEEDOR=deepseek

DEEPSEEK_API_KEY=
DEEPSEEK_API_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-pro

ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-opus-4-8
ANTHROPIC_EFFORT=high

QWEN_API_KEY=
QWEN_MODEL=qwen-plus
QWEN_API_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1

# Gemini (Google) por API key, vía su endpoint compatible con OpenAI.
# Para Vertex AI / Google Enterprise, apuntá GEMINI_API_URL al endpoint que uses.
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
GEMINI_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# Precios por millón de tokens, para que la UI pueda calcular el gasto.
# Vacíos = la UI muestra tokens pero el costo como '—'.
# PRECIO_DEEPSEEK_V4_PRO_IN=
# PRECIO_DEEPSEEK_V4_PRO_OUT=
"""


def cargar_env() -> Path:
    """Carga el .env de la carpeta de datos en el entorno del proceso.

    Lo llaman todos los puntos de entrada. Los módulos de core hacen un
    load_dotenv() sin argumentos, que no pisa lo ya cargado: por eso alcanza
    con que esto corra primero."""
    from dotenv import load_dotenv
    migrar_env_si_hace_falta()
    ruta = ruta_env()
    load_dotenv(ruta, override=True)
    return ruta


# --- Diagnóstico ------------------------------------------------------------

def _escribible(ruta: Path) -> bool:
    try:
        ruta.mkdir(parents=True, exist_ok=True)
        prueba = ruta / ".prueba_escritura"
        prueba.write_text("x", encoding="utf-8")
        prueba.unlink()
        return True
    except OSError:
        return False


def diagnostico() -> Dict[str, Any]:
    """Estado de las rutas, para el panel de diagnóstico de la UI."""
    datos, app = dir_datos(), dir_app()
    env = ruta_env()

    permisos = None
    if env.exists():
        try:
            permisos = oct(env.stat().st_mode & 0o777)
        except OSError:
            pass

    return {
        "datos": str(datos),
        "datos_existe": datos.exists(),
        "datos_escribible": _escribible(datos),
        "app": str(app),
        "app_existe": (app / "core" / "chat.py").exists(),
        "env": str(env),
        "env_existe": env.exists(),
        "env_permisos": permisos,
        "subcarpetas": {sub: (datos / sub).exists() for sub in SUBCARPETAS},
    }


# Compatibilidad: el resto del código venía importando AGENT_CODE_DIR de
# core.proyectos. Se mantiene el nombre para no tocar todos los módulos.
AGENT_CODE_DIR = dir_datos()
