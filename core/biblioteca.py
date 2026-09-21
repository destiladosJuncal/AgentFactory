"""
Biblioteca compartida de módulos/herramientas reutilizables ENTRE proyectos.

Vive en $HOME/tmp/agent_code/_biblioteca/, fuera de cualquier proyecto
individual. La idea: si en el proyecto A el agente construye, por ejemplo,
un wrapper de curl, ese módulo queda publicado acá. En el proyecto B, el
agente puede listar la biblioteca, encontrarlo, leerlo y reutilizarlo en
vez de reconstruirlo (o "redescubrir" cómo se usa) desde cero.

Diseño de seguridad: igual que Herramientas, todo queda confinado a la
carpeta de la biblioteca (no se puede escapar con rutas ni tocar nada
fuera de ahí). Esta clase solo lee/escribe texto (código como string) en
su propio registro; no ejecuta nada por sí misma.
"""

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.proyectos import AGENT_CODE_DIR

from core import interprete

BIBLIOTECA_DIR = AGENT_CODE_DIR / "_biblioteca"
MAX_MODULO_CHARS = 50_000

# Ejecutar un módulo de la biblioteca corre código que escribió el LLM. Por eso
# nunca se hace in-process: siempre en un subproceso aparte, con timeout, para
# que un bucle infinito o un sys.exit() no se lleve puesta la sesión del agente.
TIMEOUT_EJECUCION = 30
MAX_SALIDA_EJECUCION = 4_000


def _slugify(texto: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', texto.strip().lower()).strip('_')
    return slug[:50] or "modulo"


class Biblioteca:
    def __init__(self, base_dir: Path = None):
        self.base_dir = Path(base_dir) if base_dir else BIBLIOTECA_DIR
        self.modulos_dir = self.base_dir / "modulos"
        self.modulos_dir.mkdir(parents=True, exist_ok=True)
        self.registro_path = self.base_dir / "registro.json"
        if not self.registro_path.exists():
            self.registro_path.write_text("[]", encoding='utf-8')

    def _cargar_registro(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.registro_path.read_text(encoding='utf-8'))
        except Exception:
            return []

    def _guardar_registro(self, registro: List[Dict[str, Any]]):
        self.registro_path.write_text(
            json.dumps(registro, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    def listar(self) -> Dict[str, Any]:
        registro = self._cargar_registro()
        resumen = [
            {"nombre": m["nombre"], "descripcion": m["descripcion"],
             "dependencias": m.get("dependencias", [])}
            for m in registro
        ]
        return {"modulos": resumen, "total": len(resumen)}

    def leer_modulo(self, nombre: str) -> Dict[str, Any]:
        registro = self._cargar_registro()
        entrada = next((m for m in registro if m["nombre"] == nombre), None)
        if not entrada:
            return {"error": f"No existe un módulo llamado '{nombre}' en la biblioteca. Usá listar_biblioteca para ver los disponibles."}

        archivo = self.modulos_dir / entrada["archivo"]
        if not archivo.exists():
            return {"error": f"El archivo del módulo '{nombre}' no se encuentra (registro desincronizado)"}

        contenido = archivo.read_text(encoding='utf-8', errors='replace')
        return {
            "nombre": nombre,
            "descripcion": entrada["descripcion"],
            "dependencias": entrada.get("dependencias", []),
            "contenido": contenido
        }

    def publicar(self, nombre: str, descripcion: str, contenido: str,
                 dependencias: List[str] = None) -> Dict[str, Any]:
        if not nombre or not contenido:
            return {"error": "nombre y contenido son obligatorios"}
        if len(contenido) > MAX_MODULO_CHARS:
            return {"error": f"Contenido demasiado grande (máx {MAX_MODULO_CHARS} caracteres)"}

        registro = self._cargar_registro()
        existente = next((m for m in registro if m["nombre"] == nombre), None)

        if existente:
            archivo_nombre = existente["archivo"]
        else:
            # Dos nombres distintos pueden slugificar igual ('cliente curl' y
            # 'cliente_curl'): sin desambiguar, el segundo pisaría el código del
            # primero y el registro quedaría con dos entradas al mismo archivo.
            slug = _slugify(nombre)
            ocupados = {m["archivo"] for m in registro}
            archivo_nombre = f"{slug}.py"
            i = 2
            while archivo_nombre in ocupados or (self.modulos_dir / archivo_nombre).exists():
                archivo_nombre = f"{slug}_{i}.py"
                i += 1

        (self.modulos_dir / archivo_nombre).write_text(contenido, encoding='utf-8')

        ahora = time.strftime("%Y-%m-%d %H:%M:%S")
        if existente:
            existente.update({
                "descripcion": descripcion,
                "archivo": archivo_nombre,
                "dependencias": dependencias or [],
                "actualizado": ahora
            })
            accion = "actualizado"
        else:
            registro.append({
                "nombre": nombre,
                "descripcion": descripcion,
                "archivo": archivo_nombre,
                "dependencias": dependencias or [],
                "creado": ahora,
                "actualizado": ahora
            })
            accion = "publicado"

        self._guardar_registro(registro)
        return {accion: nombre, "archivo": archivo_nombre}

    def borrar_modulo(self, nombre: str) -> Dict[str, Any]:
        """Saca el módulo del registro y devuelve la ruta de su archivo, sin
        borrarlo del disco: de eso se encarga quien llama (la UI lo manda a la
        Papelera, así se puede recuperar)."""
        registro = self._cargar_registro()
        entrada = next((m for m in registro if m["nombre"] == nombre), None)
        if not entrada:
            return {"error": f"No existe un módulo llamado '{nombre}'"}

        archivo = self.modulos_dir / entrada["archivo"]
        # Si otro módulo apunta al mismo archivo (colisión de slug), no lo tocamos.
        compartido = any(m is not entrada and m.get("archivo") == entrada["archivo"]
                         for m in registro)

        registro = [m for m in registro if m is not entrada]
        self._guardar_registro(registro)

        return {
            "borrado": nombre,
            "archivo": None if compartido else (str(archivo) if archivo.exists() else None),
        }

    def ejecutar_modulo(self, nombre: str, funcion: str,
                        argumentos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Llama a una función de un módulo de la biblioteca y devuelve su
        resultado. Corre en un subproceso aislado con timeout."""
        registro = self._cargar_registro()
        entrada = next((m for m in registro if m["nombre"] == nombre), None)
        if not entrada:
            return {"error": f"No existe un módulo llamado '{nombre}' en la biblioteca. "
                             f"Usá listar_biblioteca para ver los disponibles."}
        if not funcion:
            return {"error": "Falta el nombre de la función a llamar"}

        archivo = self.modulos_dir / entrada["archivo"]
        if not archivo.exists():
            return {"error": f"El archivo del módulo '{nombre}' no se encuentra"}

        argumentos = argumentos if isinstance(argumentos, dict) else {}

        runner = (
            "import importlib.util, json, sys\n"
            "ruta, funcion, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n"
            "spec = importlib.util.spec_from_file_location('modulo_biblioteca', ruta)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "fn = getattr(mod, funcion, None)\n"
            "if fn is None:\n"
            "    disponibles = [n for n in dir(mod) if not n.startswith('_') and callable(getattr(mod, n))]\n"
            "    print('__ERROR__' + json.dumps({'error': \"no existe la funcion '\" + funcion + \"'\", 'funciones_disponibles': disponibles}))\n"
            "    sys.exit(2)\n"
            "valor = fn(**args)\n"
            "try:\n"
            "    serializado = json.dumps(valor, ensure_ascii=False, default=str)\n"
            "except Exception:\n"
            "    serializado = json.dumps(repr(valor))\n"
            "print('__RESULTADO__' + serializado)\n"
        )

        with tempfile.TemporaryDirectory(prefix="biblioteca_") as tmp:
            script = Path(tmp) / "_runner.py"
            script.write_text(runner, encoding='utf-8')
            try:
                proc = subprocess.run(
                    [interprete.interpreter(), str(script), str(archivo), funcion,
                     json.dumps(argumentos, ensure_ascii=False)],
                    capture_output=True, text=True,
                    timeout=TIMEOUT_EJECUCION, cwd=tmp,
                )
            except subprocess.TimeoutExpired:
                return {"error": f"'{nombre}.{funcion}' excedió los {TIMEOUT_EJECUCION}s y se cortó"}
            except Exception as e:
                return {"error": f"No pude ejecutar el módulo: {e}"}

        salida = proc.stdout or ""
        resultado: Dict[str, Any] = {"modulo": nombre, "funcion": funcion}

        for linea in salida.splitlines():
            if linea.startswith("__RESULTADO__"):
                crudo = linea[len("__RESULTADO__"):]
                try:
                    resultado["resultado"] = json.loads(crudo)
                except json.JSONDecodeError:
                    resultado["resultado"] = crudo
            elif linea.startswith("__ERROR__"):
                try:
                    return dict(json.loads(linea[len("__ERROR__"):]), modulo=nombre)
                except json.JSONDecodeError:
                    pass

        impresa = "\n".join(l for l in salida.splitlines()
                            if not l.startswith(("__RESULTADO__", "__ERROR__")))
        if impresa:
            resultado["stdout"] = impresa[:MAX_SALIDA_EJECUCION]

        if proc.returncode != 0 and "resultado" not in resultado:
            return {"error": f"'{nombre}.{funcion}' falló (código {proc.returncode})",
                    "detalle": (proc.stderr or "")[:MAX_SALIDA_EJECUCION]}

        if proc.stderr:
            resultado["stderr"] = proc.stderr[:MAX_SALIDA_EJECUCION]

        return resultado

    def ejecutar_tool(self, nombre_tool: str, argumentos: dict) -> Dict[str, Any]:
        dispatch = {
            "listar_biblioteca": lambda a: self.listar(),
            "leer_modulo_biblioteca": lambda a: self.leer_modulo(a.get("nombre", "")),
            "publicar_modulo_biblioteca": lambda a: self.publicar(
                a.get("nombre", ""), a.get("descripcion", ""),
                a.get("contenido", ""), a.get("dependencias", [])
            ),
            "ejecutar_modulo_biblioteca": lambda a: self.ejecutar_modulo(
                a.get("nombre", ""), a.get("funcion", ""), a.get("argumentos") or {}
            ),
        }
        fn = dispatch.get(nombre_tool)
        if fn is None:
            return {"error": f"Herramienta de biblioteca desconocida: {nombre_tool}"}
        try:
            return fn(argumentos)
        except Exception as e:
            return {"error": f"Error inesperado: {e}"}


TOOLS_SCHEMA_BIBLIOTECA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "listar_biblioteca",
            "description": (
                "Lista los módulos/herramientas reutilizables ya construidos en "
                "proyectos anteriores (ej: un wrapper de curl, un validador de "
                "emails, un cliente HTTP). Revisá esto ANTES de reimplementar "
                "algo que podría ya existir."
            ),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "leer_modulo_biblioteca",
            "description": "Lee el código completo de un módulo de la biblioteca compartida, para reutilizarlo o adaptarlo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre exacto del módulo, tal como aparece en listar_biblioteca"}
                },
                "required": ["nombre"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publicar_modulo_biblioteca",
            "description": (
                "Guarda un módulo/función/herramienta reutilizable en la biblioteca "
                "compartida para que esté disponible en CUALQUIER proyecto futuro, "
                "no solo este. Usalo cuando construyas algo genérico (un wrapper de "
                "una herramienta CLI como curl, un validador, un cliente HTTP), no "
                "para el código de la solución puntual de este proyecto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre descriptivo y único, ej: 'cliente_curl'"},
                    "descripcion": {"type": "string", "description": "Qué hace y cómo se usa (funciones y parámetros disponibles)"},
                    "contenido": {"type": "string", "description": "Código Python completo del módulo"},
                    "dependencias": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paquetes o binarios externos que requiere, ej: ['curl']"
                    }
                },
                "required": ["nombre", "descripcion", "contenido"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_modulo_biblioteca",
            "description": (
                "EJECUTA una función de un módulo de la biblioteca compartida y te "
                "devuelve lo que retorna. Esta es la forma de USAR las herramientas ya "
                "construidas en vez de reimplementarlas: si la persona te pide hacer "
                "algo que una herramienta de la biblioteca ya sabe hacer (por ejemplo "
                "un GET con el cliente curl), leela con leer_modulo_biblioteca para ver "
                "qué funciones expone y llamala con esta herramienta. Corre aislada, con "
                "límite de 30 segundos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre exacto del módulo, tal como aparece en listar_biblioteca"},
                    "funcion": {"type": "string", "description": "Nombre de la función del módulo a llamar"},
                    "argumentos": {
                        "type": "object",
                        "description": "Argumentos con nombre para esa función, ej: {'url': 'https://ejemplo.com'}",
                        "additionalProperties": True
                    }
                },
                "required": ["nombre", "funcion"]
            }
        }
    }
]
