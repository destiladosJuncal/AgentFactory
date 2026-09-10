"""
Paquetes de Python compartidos entre conversaciones.

El problema que resuelve: cada conversación que necesitaba numpy/scipy/cv2 se
armaba su propio venv adentro del workspace. Diez conversaciones con las mismas
bibliotecas pesadas = diez copias de cientos de MB cada una.

La solución es un almacén COMPARTIDO y un overlay LOCAL solo cuando hace falta:

    $DATOS/_paquetes/py3.12/          <- compartido: se instala UNA vez
    <workspace>/.paquetes/py3.12/     <- local: solo si hay conflicto de versión

Al ejecutar, el PYTHONPATH se arma como [local, compartido]: lo local gana. Así
una conversación que necesita numpy==1.26 no rompe a las demás que usan 2.x, y
el resto de las bibliotecas las sigue compartiendo igual.

Se separa por versión de Python (py3.12, py3.9...) a propósito: los wheels con
extensiones en C no son compatibles entre versiones, y mezclarlos da errores de
import imposibles de diagnosticar.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TIMEOUT_INSTALACION = 600   # instalar scipy/torch lleva su rato


def _etiqueta_python() -> str:
    return f"py{sys.version_info.major}.{sys.version_info.minor}"


def dir_compartido() -> Path:
    """Almacén compartido por TODAS las conversaciones."""
    from core.rutas import dir_datos
    d = dir_datos() / "_paquetes" / _etiqueta_python()
    d.mkdir(parents=True, exist_ok=True)
    return d


def dir_local(workspace) -> Optional[Path]:
    """Overlay de esta conversación. Solo se usa ante un conflicto de versión."""
    if not workspace:
        return None
    return Path(workspace) / ".paquetes" / _etiqueta_python()


def normalizar(nombre: str) -> str:
    """Nombre canónico segun PEP 503 ('Pillow' y 'pillow' son el mismo paquete)."""
    return re.sub(r"[-_.]+", "-", (nombre or "").strip()).lower()


def instalados(directorio) -> Dict[str, str]:
    """{nombre_normalizado: version} leyendo los *.dist-info del directorio."""
    encontrados: Dict[str, str] = {}
    if not directorio:
        return encontrados
    d = Path(directorio)
    if not d.is_dir():
        return encontrados
    for info in d.glob("*.dist-info"):
        tronco = info.name[: -len(".dist-info")]
        if "-" not in tronco:
            continue
        nombre, _, version = tronco.rpartition("-")
        encontrados[normalizar(nombre)] = version
    return encontrados


def _parsear(requisito: str):
    """(nombre_normalizado, especificador) o (nombre, None) si no se puede parsear."""
    try:
        from packaging.requirements import Requirement
        r = Requirement(requisito)
        return normalizar(r.name), r.specifier
    except Exception:
        nombre = re.split(r"[<>=!~\[ ]", (requisito or "").strip(), 1)[0]
        return normalizar(nombre), None


def _cumple(version: str, especificador) -> bool:
    """¿La versión instalada satisface lo pedido? Sin especificador, sí."""
    if especificador is None or not str(especificador):
        return True
    try:
        from packaging.version import Version
        return especificador.contains(Version(version), prereleases=True)
    except Exception:
        return False


def pythonpath(workspace=None) -> str:
    """PYTHONPATH con lo local primero y lo compartido después."""
    partes: List[str] = []
    local = dir_local(workspace)
    if local and local.is_dir():
        partes.append(str(local))
    partes.append(str(dir_compartido()))
    heredado = os.environ.get("PYTHONPATH", "")
    if heredado:
        partes.append(heredado)
    return os.pathsep.join(partes)


def entorno(workspace=None) -> Dict[str, str]:
    """Copia del entorno con el PYTHONPATH del almacén puesto."""
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath(workspace)
    return env


def _pip_instalar(requisito: str, destino: Path) -> Tuple[bool, str]:
    destino.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(destino),
             "--upgrade", requisito],
            capture_output=True, text=True, timeout=TIMEOUT_INSTALACION)
    except subprocess.TimeoutExpired:
        return False, f"La instalación superó los {TIMEOUT_INSTALACION}s"
    except Exception as e:
        return False, f"No pude ejecutar pip: {e}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[-600:]
    return True, (proc.stdout or "").strip()[-300:]


def instalar(requisito: str, workspace=None) -> Dict[str, Any]:
    """Instala un paquete reutilizando el almacén compartido siempre que se pueda.

    - Si ya está (compartido o local) y la versión sirve: no descarga nada.
    - Si NO está: va al compartido, para que la próxima conversación lo reuse.
    - Si está pero la versión pedida choca con la compartida: se instala SOLO
      para esta conversación, sin tocar lo que usan las demás.
    """
    requisito = (requisito or "").strip()
    if not requisito:
        return {"error": "Falta el nombre del paquete (ej: 'numpy' o 'numpy==1.26.4')"}

    nombre, especificador = _parsear(requisito)
    compartido, local = dir_compartido(), dir_local(workspace)

    en_local = instalados(local)
    if nombre in en_local and _cumple(en_local[nombre], especificador):
        return {"estado": "ya_estaba", "donde": "local", "paquete": nombre,
                "version": en_local[nombre], "ahorro": "no se descargó nada"}

    en_compartido = instalados(compartido)
    if nombre in en_compartido:
        if _cumple(en_compartido[nombre], especificador):
            return {"estado": "ya_estaba", "donde": "compartida", "paquete": nombre,
                    "version": en_compartido[nombre], "ahorro": "no se descargó nada"}
        # Conflicto real: la compartida no sirve para esta conversación.
        if local is None:
            return {"error": f"'{nombre}' compartido está en {en_compartido[nombre]}, "
                             f"que no cumple '{requisito}', y no hay workspace para "
                             f"instalarlo aparte."}
        ok, detalle = _pip_instalar(requisito, local)
        if not ok:
            return {"error": f"No pude instalar '{requisito}' en local: {detalle}"}
        return {"estado": "instalado", "donde": "local", "paquete": nombre,
                "version": instalados(local).get(nombre, "?"),
                "motivo": (f"conflicto de versión: la compartida es "
                           f"{en_compartido[nombre]} y pediste '{requisito}'. Se "
                           f"instaló solo para esta conversación.")}

    ok, detalle = _pip_instalar(requisito, compartido)
    if not ok:
        return {"error": f"No pude instalar '{requisito}': {detalle}"}
    return {"estado": "instalado", "donde": "compartida", "paquete": nombre,
            "version": instalados(compartido).get(nombre, "?"),
            "nota": "queda disponible para todas las conversaciones"}


def listar(workspace=None) -> Dict[str, Any]:
    compartido = instalados(dir_compartido())
    local = instalados(dir_local(workspace))
    return {
        "compartidas": dict(sorted(compartido.items())),
        "locales_de_esta_conversacion": dict(sorted(local.items())),
        "ruta_compartida": str(dir_compartido()),
    }


# --- Tool para el chat ------------------------------------------------------

def ejecutar_tool_paquetes(nombre: str, argumentos: dict, workspace=None) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "instalar_paquete":
        return instalar(a.get("paquete", ""), workspace=workspace)
    if nombre == "listar_paquetes":
        return listar(workspace=workspace)
    return {"error": f"Herramienta de paquetes desconocida: {nombre}"}


TOOLS_SCHEMA_PAQUETES: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "instalar_paquete",
            "description": (
                "Instala una biblioteca de Python para poder usarla. USALA SIEMPRE "
                "en vez de 'pip install' por shell o de crear un venv: los paquetes "
                "van a un almacén COMPARTIDO entre conversaciones, así no se "
                "vuelven a descargar ni ocupan disco de nuevo. Si la versión que "
                "pedís choca con la compartida, se instala sola para esta "
                "conversación, automáticamente."),
            "parameters": {
                "type": "object",
                "properties": {
                    "paquete": {"type": "string",
                                "description": "Requisito pip: 'numpy', 'numpy==1.26.4', 'pandas>=2'"},
                },
                "required": ["paquete"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_paquetes",
            "description": ("Lista las bibliotecas de Python ya instaladas: las "
                            "compartidas entre conversaciones y las locales de esta. "
                            "Miralas antes de instalar algo."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
