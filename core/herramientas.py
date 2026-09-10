"""
Herramientas (tool calling) que el Generador puede pedirle al LLM que use
para explorar y construir el proyecto: buscar archivos, leerlos, listar
directorios, crear carpetas, escribir archivos y correr comandos de solo
lectura.

Diseño de seguridad:
- Cada instancia de Herramientas está confinada a un `base_dir` específico:
  la carpeta de ESE proyecto dentro de $HOME/tmp/agent_code/<proyecto>/.
  No se puede escapar con rutas absolutas ni con '..', ni tocar otros
  proyectos, el código del agente, ni el resto del filesystem.
- Se bloquea la lectura/escritura de archivos cuyo nombre sugiera secretos
  (.env, *secret*, *key*, credenciales, tokens, etc).
- escribir_archivo tiene límite de tamaño y solo puede crear/sobreescribir
  DENTRO de base_dir.
- ejecutar_comando sigue siendo de solo lectura (whitelist, sin shell) y
  nunca puede modificar nada por sí mismo; las escrituras solo pasan por
  escribir_archivo/crear_carpeta, que están explícitamente controladas.
"""

import subprocess
import shlex
from pathlib import Path
from typing import Dict, Any, List, Optional

MAX_ARCHIVO_CHARS = 20_000
MAX_ESCRITURA_CHARS = 200_000
MAX_RESULTADOS_BUSQUEDA = 50
MAX_SALIDA_COMANDO = 4_000
TIMEOUT_COMANDO = 5

# macOS/Linux toleran hasta 255 bytes por componente de ruta. Cuando el modelo
# se confunde de argumento y manda el CONTENIDO donde va la RUTA, el error que
# sale es un OSError críptico ([Errno 63]); es mucho más útil decirle qué se
# equivocó para que se corrija solo en la vuelta siguiente.
MAX_LARGO_NOMBRE = 255


def revisar_ruta(ruta: str, campo: str = "ruta") -> Optional[str]:
    """Devuelve un mensaje de error si el valor no parece una ruta, o None."""
    if not isinstance(ruta, str):
        return f"'{campo}' tiene que ser texto"
    if "\x00" in ruta:
        return f"'{campo}' tiene caracteres nulos"
    if "\n" in ruta or "\r" in ruta:
        return (f"'{campo}' tiene saltos de línea, así que no es una ruta. "
                f"Parece que mandaste el CONTENIDO del archivo en '{campo}'. "
                f"En escribir_archivo la ruta va en 'ruta' y el código en "
                f"'contenido'; para correr un script usá ejecutar_python con "
                f"el código en 'codigo'.")
    if any(len(parte.encode("utf-8")) > MAX_LARGO_NOMBRE for parte in ruta.split("/")):
        return (f"'{campo}' tiene un tramo de más de {MAX_LARGO_NOMBRE} caracteres, "
                f"que el sistema de archivos no admite. Si lo que querías era "
                f"escribir contenido, va en 'contenido', no en '{campo}'.")
    return None


PATRONES_BLOQUEADOS = ('.env', 'secret', 'credential', 'password', 'token', '.pem', '.key', 'id_rsa')

# Whitelist de solo lectura para el MODO ITERATIVO (core/generador.py), que
# es un contexto acotado: el modelo explora el proyecto, no opera la máquina.
#
# 'python3' y 'pip' salieron de acá a propósito. Estaban permitidos y hacían que
# esta whitelist fuera decorativa: `python3 -c "..."` ejecutaba cualquier cosa
# en cualquier lado. Las conversaciones ahora tienen ejecución de verdad y
# declarada en core/ejecucion.py; no hace falta esta puerta lateral.
COMANDOS_PERMITIDOS = {
    'ls', 'cat', 'find', 'grep', 'wc', 'head', 'tail', 'tree',
}


class Herramientas:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolver_ruta_segura(self, ruta_relativa: str) -> Path:
        aviso = revisar_ruta(ruta_relativa)
        if aviso:
            raise ValueError(aviso)
        candidata = (self.base_dir / ruta_relativa).resolve()
        if candidata != self.base_dir and self.base_dir not in candidata.parents:
            raise PermissionError(f"Ruta fuera del proyecto no permitida: {ruta_relativa}")
        return candidata

    def _es_archivo_sensible(self, ruta: Path) -> bool:
        nombre = ruta.name.lower()
        return any(p in nombre for p in PATRONES_BLOQUEADOS)

    # --- Lectura ---

    def buscar_archivos(self, patron: str, directorio: str = ".") -> Dict[str, Any]:
        try:
            base = self._resolver_ruta_segura(directorio)
            if not base.is_dir():
                return {"error": f"'{directorio}' no es un directorio válido"}

            resultados = []
            for p in base.glob(patron):
                if self._es_archivo_sensible(p):
                    continue
                try:
                    rel = p.resolve().relative_to(self.base_dir)
                except ValueError:
                    continue
                resultados.append(str(rel))
                if len(resultados) >= MAX_RESULTADOS_BUSQUEDA:
                    break

            return {"encontrados": resultados, "total": len(resultados)}
        except (PermissionError, ValueError) as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Error buscando archivos: {e}"}

    def leer_archivo(self, ruta: str) -> Dict[str, Any]:
        try:
            archivo = self._resolver_ruta_segura(ruta)

            if self._es_archivo_sensible(archivo):
                return {"error": "Acceso denegado: este archivo parece contener información sensible"}

            if not archivo.is_file():
                return {"error": f"'{ruta}' no existe o no es un archivo"}

            contenido = archivo.read_text(encoding='utf-8', errors='replace')
            truncado = len(contenido) > MAX_ARCHIVO_CHARS
            if truncado:
                contenido = contenido[:MAX_ARCHIVO_CHARS]

            return {"contenido": contenido, "truncado": truncado, "longitud_total": len(contenido)}
        except (PermissionError, ValueError) as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Error leyendo archivo: {e}"}

    def listar_directorio(self, ruta: str = ".") -> Dict[str, Any]:
        try:
            directorio = self._resolver_ruta_segura(ruta)
            if not directorio.is_dir():
                return {"error": f"'{ruta}' no es un directorio válido"}

            entradas = []
            for item in sorted(directorio.iterdir()):
                if self._es_archivo_sensible(item):
                    continue
                entradas.append({
                    "nombre": item.name,
                    "tipo": "directorio" if item.is_dir() else "archivo"
                })

            return {"entradas": entradas}
        except (PermissionError, ValueError) as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Error listando directorio: {e}"}

    # --- Escritura (siempre confinada a base_dir) ---

    def crear_carpeta(self, ruta: str) -> Dict[str, Any]:
        try:
            if not ruta or ruta.strip() in (".", ""):
                return {"error": "Ruta de carpeta inválida"}
            carpeta = self._resolver_ruta_segura(ruta)
            carpeta.mkdir(parents=True, exist_ok=True)
            return {"creada": str(carpeta.relative_to(self.base_dir))}
        except (PermissionError, ValueError) as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Error creando carpeta: {e}"}

    def escribir_archivo(self, ruta: str, contenido: str) -> Dict[str, Any]:
        try:
            if not ruta or ruta.strip() in (".", ""):
                return {"error": "Ruta de archivo inválida"}
            archivo = self._resolver_ruta_segura(ruta)

            if self._es_archivo_sensible(archivo):
                return {"error": "No se permite escribir archivos con nombres que parecen contener secretos"}

            if len(contenido or "") > MAX_ESCRITURA_CHARS:
                return {"error": f"Contenido demasiado grande (máx {MAX_ESCRITURA_CHARS} caracteres)"}

            archivo.parent.mkdir(parents=True, exist_ok=True)
            archivo.write_text(contenido or "", encoding='utf-8')

            return {
                "escrito": str(archivo.relative_to(self.base_dir)),
                "bytes": len((contenido or "").encode('utf-8'))
            }
        except (PermissionError, ValueError) as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Error escribiendo archivo: {e}"}

    # --- Comandos de solo lectura ---

    def ejecutar_comando(self, comando: str) -> Dict[str, Any]:
        try:
            partes = shlex.split(comando)
        except ValueError as e:
            return {"error": f"Comando inválido: {e}"}

        if not partes:
            return {"error": "Comando vacío"}

        binario = partes[0]
        if binario not in COMANDOS_PERMITIDOS:
            return {"error": f"Comando no permitido: '{binario}'. Permitidos: {sorted(COMANDOS_PERMITIDOS)}"}

        # No hay shell (subprocess con shell=False), así que pipes, redirecciones
        # y '~' viajarían como nombres de archivo literales y el comando haría
        # cualquier cosa en silencio. Mejor rechazarlo con una explicación.
        metacaracteres = [p for p in partes[1:] if p in ('|', '>', '<', '>>', '&&', '||', ';')
                          or p.startswith(('2>', '1>', '&>'))]
        if metacaracteres:
            return {"error": (
                f"Acá no hay shell: {', '.join(repr(m) for m in metacaracteres)} se pasaría "
                f"como nombre de archivo, no como redirección o pipe. Corré el comando solo "
                f"(el stderr ya te lo devuelvo junto con el stdout) y filtrá vos el resultado."
            )}
        if any(p.startswith('~') for p in partes[1:]):
            return {"error": (
                "Acá no hay shell, así que '~' no se expande y quedaría como un directorio "
                "literal. Usá la ruta absoluta (por ejemplo /Users/<usuario>/Downloads)."
            )}

        try:
            resultado = subprocess.run(
                partes,
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_COMANDO,
                shell=False
            )
            salida = (resultado.stdout or "") + (resultado.stderr or "")
            truncado = len(salida) > MAX_SALIDA_COMANDO
            if truncado:
                salida = salida[:MAX_SALIDA_COMANDO]

            return {"salida": salida, "codigo_retorno": resultado.returncode, "truncado": truncado}
        except subprocess.TimeoutExpired:
            return {"error": "Comando excedió el tiempo límite"}
        except FileNotFoundError:
            return {"error": f"Binario no encontrado: '{binario}'"}
        except Exception as e:
            return {"error": f"Error ejecutando comando: {e}"}

    def ejecutar_tool(self, nombre: str, argumentos: dict) -> Dict[str, Any]:
        """Despacha la llamada a la herramienta correspondiente."""
        dispatch = {
            "buscar_archivos": lambda a: self.buscar_archivos(a.get("patron", ""), a.get("directorio", ".")),
            "leer_archivo": lambda a: self.leer_archivo(a.get("ruta", "")),
            "listar_directorio": lambda a: self.listar_directorio(a.get("ruta", ".")),
            "crear_carpeta": lambda a: self.crear_carpeta(a.get("ruta", "")),
            "escribir_archivo": lambda a: self.escribir_archivo(a.get("ruta", ""), a.get("contenido", "")),
            "ejecutar_comando": lambda a: self.ejecutar_comando(a.get("comando", "")),
        }
        fn = dispatch.get(nombre)
        if fn is None:
            return {"error": f"Herramienta desconocida: {nombre}"}
        try:
            return fn(argumentos)
        except Exception as e:
            return {"error": f"Error inesperado ejecutando '{nombre}': {e}"}


# Esquema de herramientas en formato OpenAI/DeepSeek function-calling.
# No depende de la instancia: es el mismo catálogo para cualquier proyecto.
TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "buscar_archivos",
            "description": "Busca archivos por patrón glob dentro del proyecto actual (ej: '**/*.py'). Útil para ver qué se generó en iteraciones previas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patron": {"type": "string", "description": "Patrón glob, ej: '**/*.py' o 'iteraciones/*.py'"},
                    "directorio": {"type": "string", "description": "Directorio desde donde buscar, relativo al proyecto. Default: '.'"}
                },
                "required": ["patron"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "leer_archivo",
            "description": "Lee el contenido de un archivo de texto dentro del proyecto actual (código de iteraciones previas, config, notas, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta relativa a la raíz del proyecto"}
                },
                "required": ["ruta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "listar_directorio",
            "description": "Lista archivos y carpetas dentro de un directorio del proyecto actual.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta relativa. Default: '.'"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "crear_carpeta",
            "description": "Crea una carpeta (y las carpetas padre necesarias) dentro del proyecto actual.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta relativa dentro del proyecto"}
                },
                "required": ["ruta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escribir_archivo",
            "description": "Crea o sobreescribe un archivo dentro del proyecto actual con el contenido dado. Útil para construir archivos auxiliares (datos de ejemplo, notas, módulos secundarios). El código de la solución final NO hace falta escribirlo con esta herramienta: se guarda automáticamente cuando respondés con el bloque ```python.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta relativa dentro del proyecto"},
                    "contenido": {"type": "string", "description": "Contenido completo del archivo"}
                },
                "required": ["ruta", "contenido"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_comando",
            "description": (
                "Ejecuta un comando de solo lectura (whitelist: ls, cat, find, grep, wc, "
                "head, tail, tree). IMPORTANTE: NO hay shell. No funcionan pipes (|), "
                "redirecciones (>, 2>/dev/null), comodines del shell ni '~' — todo eso "
                "se pasaría como nombre de archivo literal. Usá rutas absolutas y un solo "
                "comando por llamada; el stderr ya viene incluido en la salida, así que no "
                "hace falta redirigirlo. Timeout: 5 segundos, así que evitá barridos como "
                "'find /' y acotá con -maxdepth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "comando": {"type": "string", "description": "Un comando, sin pipes ni redirecciones. Ej: 'grep -rn def iteraciones/'"}
                },
                "required": ["comando"]
            }
        }
    }
]
