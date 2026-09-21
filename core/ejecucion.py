"""
Ejecución real de shell y Python desde la conversación.

Reemplaza al viejo `ejecutar_comando` de core/herramientas.py, que decía ser
"solo lectura" con una whitelist pero dejaba pasar `python3 -c` — o sea que en
los hechos ya ejecutaba cualquier cosa, sin que nadie lo hubiera decidido.
Acá la capacidad es explícita: el agente puede correr lo que vos podrías correr
desde una terminal.

El único freno es el que importa: **todo lo que borre archivos se detiene y te
pregunta**. No es un sandbox —no lo es y no pretende serlo—, es una traba en la
única operación que no se puede deshacer.

Cómo funciona la confirmación:

    ejecucion.CONFIRMADOR = mi_funcion     # la setea la UI (o la consola)

    mi_funcion(resumen, detalle, clave) -> 'permitir' | 'siempre' | 'no'

Si nadie registró un confirmador, las operaciones destructivas se RECHAZAN.
El default es no romper nada.
"""

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from core import plataforma
from core.herramientas import revisar_ruta
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import interprete

TIMEOUT_DEFAULT = 60
TIMEOUT_MAXIMO = 600
MAX_SALIDA = 8_000

# --- Detección de operaciones destructivas ---------------------------------

# (clave, regex, explicación). La clave es lo que se recuerda cuando elegís
# "permitir siempre", así que identifica al TIPO de operación, no al comando
# exacto: aprobar un `rm` no aprueba un `git reset --hard`.
PATRONES_DESTRUCTIVOS: List[Tuple[str, str, str]] = [
    ("rm",            r"(?:^|[;&|]|\s)\s*rm\b",            "borra archivos (rm)"),
    ("rmdir",         r"(?:^|[;&|]|\s)\s*rmdir\b",         "borra directorios (rmdir)"),
    ("unlink",        r"(?:^|[;&|]|\s)\s*unlink\b",        "borra un archivo (unlink)"),
    ("shred",         r"(?:^|[;&|]|\s)\s*(?:shred|srm)\b", "borra de forma irrecuperable"),
    ("mv",            r"(?:^|[;&|]|\s)\s*mv\b",            "mueve archivos (puede pisar el destino)"),
    ("find-delete",   r"\bfind\b.*?(?:-delete|-exec\s+rm)", "borra archivos encontrados por find"),
    ("truncate",      r"(?:^|[;&|]|\s)\s*truncate\b",      "vacía archivos (truncate)"),
    ("dd",            r"(?:^|[;&|]|\s)\s*dd\b.*\bof=",     "escribe crudo sobre un destino (dd)"),
    ("mkfs",          r"\bmkfs\b|\bdiskutil\s+erase",      "formatea un volumen"),
    ("git-destruct",  r"\bgit\s+(?:clean|reset\s+--hard|checkout\s+--|branch\s+-D)",
                      "descarta cambios o ramas de git de forma irreversible"),
    ("db-drop",       r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", "borra tablas o bases de datos"),
    ("redirect",      r"(?<![>\d])>(?!>)\s*[^\s|&;]+",     "sobrescribe un archivo con '>'"),
]

# --- Lectura de credenciales -----------------------------------------------
#
# Los headers de la captura se guardan cifrados (core/secretos.py), así que un
# SELECT crudo devuelve bytes ilegibles: la exposición accidental ya no existe.
# Lo que falta cubrir es el acceso DELIBERADO — que el agente vaya a buscar la
# clave o llame al módulo que descifra.
#
# El control es de consentimiento, no criptográfico, y conviene tenerlo claro:
# la inspección es estática y se puede evadir armando la ruta por pedazos o
# haciendo un glob de la carpeta. Lo que consigue es que el camino normal pida
# permiso y quede registrado. Y tiene un efecto útil de rebote: en el uso
# legítimo el agente escribe el acceso derecho y el diálogo aparece; si llega
# ofuscado, eso mismo es la señal de que algo lo está manipulando.
PATRONES_CREDENCIALES: List[Tuple[str, str, str]] = [
    ("credenciales", r"clave-captura\.key|core\.secretos|from\s+core\s+import[^\n]*\bsecretos\b|secretos\s*\.\s*(?:decrypt|key|key_path)",
     "lee o descifra el almacén de credenciales de la captura"),
    ("captura-cruda", r"sesion\.db|_proxy[/\\]",
     "abre la base de la captura directamente, sin pasar por las herramientas"),
]


# Lo mismo del lado de Python.
PATRONES_DESTRUCTIVOS_PY: List[Tuple[str, str, str]] = [
    ("py-rmtree",  r"\bshutil\s*\.\s*rmtree\b",                     "borra un árbol de directorios (shutil.rmtree)"),
    ("py-remove",  r"\bos\s*\.\s*(?:remove|unlink|removedirs|rmdir)\b", "borra archivos (os.remove/unlink)"),
    ("py-unlink",  r"\.\s*unlink\s*\(",                             "borra un archivo (Path.unlink)"),
    ("py-move",    r"\bshutil\s*\.\s*move\b",                       "mueve archivos (puede pisar el destino)"),
    ("py-system",  r"\bos\s*\.\s*system\b|\bsubprocess\b",          "lanza comandos del sistema desde Python"),
    ("py-truncate", r"\.\s*truncate\s*\(",                          "vacía un archivo (truncate)"),
]


def analizar_riesgo(texto: str, es_python: bool = False) -> List[Tuple[str, str]]:
    """Devuelve [(clave, explicación)] de las operaciones destructivas detectadas."""
    patrones = PATRONES_DESTRUCTIVOS_PY if es_python else PATRONES_DESTRUCTIVOS
    if es_python:
        # Un script Python también puede traer shell adentro: miramos los dos.
        patrones = patrones + PATRONES_DESTRUCTIVOS
    # Los patrones de arriba son todos de Unix. En Windows, `del`, `Remove-Item`
    # o `robocopy /MIR` no se parecen a ninguno y pasaban SIN pedir confirmación:
    # el guard existía pero no cubría el sistema en el que estaba corriendo.
    patrones = patrones + plataforma.patrones_destructivos_del_sistema()
    # Leer credenciales no borra nada, pero es igual de irreversible: una cookie
    # de sesión que salió no vuelve. Va por el mismo diálogo.
    patrones = patrones + PATRONES_CREDENCIALES
    hallazgos, vistos = [], set()
    for clave, patron, explicacion in patrones:
        if clave in vistos:
            continue
        if re.search(patron, texto, flags=re.IGNORECASE):
            vistos.add(clave)
            hallazgos.append((clave, explicacion))
    return hallazgos


# --- Elevación de privilegios ------------------------------------------

# Se usa el diálogo de autenticación del SISTEMA (osascript + "with
# administrator privileges"), nunca uno propio. Dos razones:
#
#  1. La contraseña la maneja macOS y jamás llega a este proceso. Un cuadro
#     de diálogo propio la tendría en memoria, en variables y quizá en logs.
#  2. Una ventana hecha por la app pidiendo la clave de admin es la forma
#     exacta del malware que roba credenciales. No hay que enseñarle a nadie
#     a escribir su contraseña en ventanas que no son del sistema.
#
# Además soporta Touch ID sin que tengamos que hacer nada.

ERROR_CANCELADO = -128
ERROR_CREDENCIAL = -60007


def _escapar_applescript(texto: str) -> str:
    """Escapa para meter un comando dentro de un string de AppleScript.

    Solo hay que cuidar la barra invertida y la comilla doble; el resto viaja
    literal. El argumento llega a osascript por argv, así que no interviene
    ningún shell en el medio."""
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def ejecutar_como_admin(comando: str, cwd: Path,
                        timeout: int = TIMEOUT_DEFAULT) -> Dict[str, Any]:
    """Corre el comando con privilegios de administrador.

    macOS muestra su propio diálogo de autenticación. Si la persona cancela,
    vuelve un error normal y no pasa nada más."""
    if not plataforma.ES_MAC:
        return {"error": "La elevación de privilegios solo está implementada en macOS"}

    completo = f"cd {shlex.quote(str(cwd))} && {comando}"
    guion = (f'do shell script "{_escapar_applescript(completo)}" '
             f'with administrator privileges')

    try:
        proceso = subprocess.run(["osascript", "-e", guion],
                                 capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"El comando con privilegios excedió {timeout}s"}
    except Exception as e:
        return {"error": f"No pude pedir privilegios: {e}"}

    if proceso.returncode != 0:
        detalle = (proceso.stderr or "").strip()
        if str(ERROR_CANCELADO) in detalle or "User canceled" in detalle:
            return {"error": "Cancelaste la autenticación: no se ejecutó nada."}
        if str(ERROR_CREDENCIAL) in detalle:
            return {"error": "Contraseña o usuario incorrectos: no se ejecutó nada."}
        return {"error": f"Falló la ejecución con privilegios: {detalle[:400]}"}

    # osascript devuelve TODO por stdout, sin separar stderr del comando.
    return {"estado": "exito", "stdout": (proceso.stdout or "").strip()[:MAX_SALIDA],
            "stderr": "", "codigo_retorno": 0, "como_admin": True}


# --- Confirmación ----------------------------------------------------------

# La UI (o la consola) registra acá su forma de preguntar.
CONFIRMADOR: Optional[Callable[[str, str, str], str]] = None

# Fallback para los modos que no tienen conversación (consola suelta, tests).
# Las conversaciones traen su PROPIO set: aprobar `rm` en una conversación no
# lo aprueba en las demás. No se persiste a disco a propósito: al reabrir la
# app se vuelve a preguntar.
_APROBADAS_SIEMPRE: set = set()


def _rutas_de(comando: str, cwd: Path) -> Dict[str, List[str]]:
    """Intenta separar sobre qué archivos opera el comando: de dónde lee y
    dónde escribe. Es una lectura best-effort para mostrarte en el diálogo, no
    una garantía — el comando real puede tocar más cosas."""
    origen: List[str] = []
    destino: List[str] = []

    # Redirecciones: lo que va después de '>' se sobrescribe.
    for m in re.finditer(r"(?<![>\d])>>?\s*([^\s|&;]+)", comando):
        destino.append(m.group(1))

    try:
        # shlex en modo POSIX trata '\' como escape: 'del C:\Users\pc\x.txt'
        # se convertiría en 'C:Userspcx.txt'. Esto alimenta el texto que la
        # persona LEE para decidir si aprueba un comando destructivo, así que
        # mostrar la ruta equivocada no es cosmético.
        partes = shlex.split(comando, posix=not plataforma.ES_WINDOWS)
        if plataforma.ES_WINDOWS:
            # En modo no-POSIX las comillas quedan pegadas al token.
            partes = [p.strip('"') for p in partes]
    except ValueError:
        partes = comando.split()

    argumentos = [p for p in partes[1:]
                  if not p.startswith(("-", "/")) and p not in (">", ">>", "|", "&&", ";")]
    binario = Path(partes[0]).name.lower() if partes else ""
    # En Windows el binario puede venir con extensión: robocopy.exe -> robocopy
    if plataforma.ES_WINDOWS and binario.endswith(".exe"):
        binario = binario[:-4]

    if binario == "robocopy" and len(argumentos) >= 2:
        # robocopy ORIGEN DESTINO [archivos...]: el destino es el segundo, no
        # el último. Importa porque con /MIR el destino es lo que se BORRA.
        origen.append(argumentos[0])
        destino.append(argumentos[1])
    elif binario in ("mv", "cp", "rsync", "install",
                     "move", "copy", "xcopy") and len(argumentos) >= 2:
        origen += argumentos[:-1]
        destino.append(argumentos[-1])
    elif binario in ("rm", "rmdir", "unlink", "shred", "srm", "truncate",
                     "del", "erase", "rd"):
        destino += argumentos
    elif argumentos:
        origen += argumentos

    def _absoluta(r: str) -> str:
        p = Path(r).expanduser()
        return str(p if p.is_absolute() else (cwd / p))

    limpiar = lambda lista: sorted({_absoluta(r) for r in lista if r})  # noqa: E731
    return {"origen": limpiar(origen), "destino": limpiar(destino)}


def _describir_rutas(rutas: Dict[str, List[str]]) -> str:
    lineas = []
    if rutas.get("origen"):
        lineas.append("Lee / toma de:")
        lineas += [f"    {r}{'  (no existe)' if not Path(r).exists() else ''}"
                   for r in rutas["origen"][:8]]
    if rutas.get("destino"):
        lineas.append("Escribe / borra en:")
        for r in rutas["destino"][:8]:
            p = Path(r)
            if p.is_dir():
                try:
                    n = sum(1 for _ in p.rglob("*"))
                    marca = f"  (CARPETA con {n} elemento(s) adentro)"
                except OSError:
                    marca = "  (carpeta)"
            elif p.exists():
                marca = f"  ({p.stat().st_size} bytes, ya existe)"
            else:
                marca = "  (no existe todavía)"
            lineas.append(f"    {r}{marca}")
    return "\n".join(lineas)


def _pedir_permiso_admin(comando: str, cwd: Path) -> Optional[Dict[str, Any]]:
    """Confirmación previa al diálogo del sistema.

    Son dos puertas a propósito: esta muestra QUÉ se va a correr y sobre qué
    rutas; la de macOS autentica. Acá nunca se ofrece 'permitir siempre':
    root se aprueba comando por comando, todas las veces."""
    if CONFIRMADOR is None:
        return {"error": "No hay forma de confirmar la elevación de privilegios; no ejecuto."}

    riesgos = analizar_riesgo(comando, es_python=False)
    aviso = "; ".join(e for _, e in riesgos)
    detalle = (f"Directorio de trabajo:\n    {cwd}\n\n"
               f"{comando}\n\n"
               f"{_describir_rutas(_rutas_de(comando, cwd))}")
    if aviso:
        detalle += f"\n\n⚠️ Además: {aviso}"
    detalle += ("\n\nDespués de aceptar acá, macOS va a pedirte autenticación "
                "en su propio diálogo. Tu contraseña no pasa por esta app.")

    decision = CONFIRMADOR("se ejecuta como ADMINISTRADOR (root)", detalle, "admin")
    # 'siempre' se trata igual que 'permitir': no se recuerda.
    if decision in ("permitir", "siempre"):
        return None
    return {"error": "No autorizaste la ejecución como administrador."}


def registrar_confirmador(fn: Optional[Callable[[str, str, str], str]]):
    global CONFIRMADOR
    CONFIRMADOR = fn


def olvidar_aprobaciones():
    _APROBADAS_SIEMPRE.clear()


def aprobaciones_vigentes() -> List[str]:
    return sorted(_APROBADAS_SIEMPRE)


def confirmador_consola(resumen: str, detalle: str, clave: str) -> str:
    """Confirmador para los modos de consola (main_chat.py)."""
    print(f"\n⚠️  El agente quiere ejecutar algo que {resumen}")
    print(f"    {detalle}")
    print("    [p] permitir una vez · [s] permitir siempre · [n] no")
    try:
        respuesta = input("    > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    return {"p": "permitir", "s": "siempre", "n": "no"}.get(respuesta, "no")


def _pedir_permiso(texto: str, es_python: bool, cwd: Path,
                   permisos: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """None si se puede seguir; un dict de error si hay que abortar.

    `permisos` es el set de "permitir siempre" de ESTA conversación."""
    aprobadas = _APROBADAS_SIEMPRE if permisos is None else permisos
    hallazgos = [(c, e) for c, e in analizar_riesgo(texto, es_python)
                 if c not in aprobadas]
    if not hallazgos:
        return None

    if CONFIRMADOR is None:
        return {"error": (
            "Bloqueado: esto borra o sobrescribe cosas y no hay forma de pedirte "
            "confirmación en este modo. Detectado: "
            + "; ".join(e for _, e in hallazgos)
        ), "requeria_confirmacion": True}

    resumen = "; ".join(e for _, e in hallazgos)
    clave_principal = hallazgos[0][0]

    # El detalle que ve el usuario: el comando textual MÁS sobre qué rutas opera.
    descripcion_rutas = _describir_rutas(_rutas_de(texto, cwd)) if not es_python else ""
    detalle = texto if not descripcion_rutas else f"{texto}\n\n{descripcion_rutas}"
    detalle = f"Directorio de trabajo:\n    {cwd}\n\n{detalle}"

    decision = CONFIRMADOR(resumen, detalle, clave_principal)

    if decision == "siempre":
        aprobadas.update(c for c, _ in hallazgos)
        return None
    if decision == "permitir":
        return None
    return {"error": f"El usuario no autorizó esta operación ({resumen}). "
                     f"No se ejecutó nada.",
            "rechazado_por_el_usuario": True}


def pedir_permiso_simple(clave: str, resumen: str, detalle: str,
                         permisos: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """Confirmación para una operación que no es un comando de shell.

    Existe para reusar el mismo mecanismo —y el mismo diálogo— en operaciones
    que hay que confirmar pero no pasan por analizar_riesgo(). La primera es la
    instalación de paquetes: el agente puede traer código de terceros de PyPI,
    y hasta ahora lo hacía sin que nadie lo viera.

    Mismo contrato que _pedir_permiso: None si se puede seguir, un dict de
    error si hay que abortar. 'siempre' se recuerda por conversación y no se
    persiste a disco."""
    aprobadas = _APROBADAS_SIEMPRE if permisos is None else permisos
    if clave in aprobadas:
        return None

    if CONFIRMADOR is None:
        return {"error": (f"Bloqueado: {resumen}. No hay forma de pedirte "
                          f"confirmación en este modo."),
                "requeria_confirmacion": True}

    decision = CONFIRMADOR(resumen, detalle, clave)
    if decision == "siempre":
        aprobadas.add(clave)
        return None
    if decision == "permitir":
        return None
    return {"error": f"El usuario no autorizó esta operación ({resumen}). "
                     f"No se instaló ni ejecutó nada.",
            "rechazado_por_el_usuario": True}


# --- Ejecución -------------------------------------------------------------

def _resultado(proc, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    salida = (proc.stdout or "")
    error = (proc.stderr or "")
    truncado = len(salida) > MAX_SALIDA or len(error) > MAX_SALIDA
    resultado: Dict[str, Any] = {
        "codigo_retorno": proc.returncode,
        "stdout": salida[:MAX_SALIDA],
        "truncado": truncado,
    }
    if error.strip():
        resultado["stderr"] = error[:MAX_SALIDA]
    if extra:
        resultado.update(extra)
    return resultado


def _timeout(valor: Any) -> int:
    try:
        return max(1, min(int(valor), TIMEOUT_MAXIMO))
    except (TypeError, ValueError):
        return TIMEOUT_DEFAULT


def _cwd(directorio: Optional[str], por_defecto: Path) -> Tuple[Optional[Path], Optional[Dict]]:
    if not directorio:
        return por_defecto, None

    aviso = revisar_ruta(directorio, "directorio")
    if aviso:
        return None, {"error": aviso}

    try:
        ruta = Path(directorio).expanduser()
        if not ruta.is_dir():
            return None, {"error": f"El directorio '{directorio[:80]}' no existe"}
    except OSError as e:
        # is_dir() levanta OSError con nombres imposibles (demasiado largos,
        # con bytes nulos). Sin este except, la excepción se escapaba del tool
        # y cortaba el turno dejando el historial roto.
        return None, {"error": f"Directorio inválido: {e}"}
    return ruta, None


def ejecutar_shell(comando: str, directorio: str = "", timeout: Any = TIMEOUT_DEFAULT,
                   base: Optional[Path] = None, permisos: Optional[set] = None,
                   como_admin: bool = False, admin_habilitado: bool = False) -> Dict[str, Any]:
    """Ejecuta un comando con shell real: pipes, redirecciones, '~', todo."""
    if not (comando or "").strip():
        return {"error": "Comando vacío"}

    cwd, error = _cwd(directorio, base or Path.home())
    if error:
        return error

    if como_admin:
        if not admin_habilitado:
            return {"error": ("Esta conversación no tiene habilitada la ejecución como "
                             "administrador. La persona puede activarla con el check "
                             "'Admin' si hace falta de verdad. Mientras tanto, probá "
                             "una alternativa que no necesite privilegios.")}
        # Con root SIEMPRE se confirma, sea destructivo o no: el daño posible
        # no se parece al de un comando normal y no hay papelera que lo salve.
        bloqueo = _pedir_permiso_admin(comando, cwd)
        if bloqueo:
            return bloqueo
        return ejecutar_como_admin(comando, cwd, _timeout(timeout))

    bloqueo = _pedir_permiso(comando, False, cwd, permisos)
    if bloqueo:
        return bloqueo

    try:
        from core import paquetes
        # shell=True en Windows significa "%COMSPEC% /c <string>", que es
        # exactamente lo que devuelve shell_por_defecto(). Se deja pasar el
        # comando como string y NO como lista: subprocess re-citaría cada
        # elemento con list2cmdline y corrompería comillas y espacios.
        # executable= solo aplica en POSIX; en Windows subprocess lo ignora
        # para shell=True, pero pasarle "/bin/bash" igual es pedir problemas.
        # En POSIX se sigue forzando bash y no $SHELL: el schema de la tool le
        # promete bash al modelo, y en una Mac moderna $SHELL es zsh.
        extra = {} if plataforma.ES_WINDOWS else {"executable": "/bin/bash"}
        proc = subprocess.run(
            comando, shell=True, cwd=str(cwd), capture_output=True, text=True,
            encoding=plataforma.codificacion_consola(), errors="replace",
            timeout=_timeout(timeout), **extra,
            # PYTHONPATH apuntando al almacén compartido (+ overlay local): así
            # un script usa las bibliotecas ya instaladas sin volver a bajarlas.
            env=paquetes.entorno(base),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"El comando excedió los {_timeout(timeout)}s y se cortó"}
    except Exception as e:
        return {"error": f"No pude ejecutar el comando: {e}"}

    return _resultado(proc, {"comando": comando, "directorio": str(cwd)})


def ejecutar_python(codigo: str, directorio: str = "", timeout: Any = TIMEOUT_DEFAULT,
                    base: Optional[Path] = None, permisos: Optional[set] = None) -> Dict[str, Any]:
    """Corre un script Python en un proceso aparte, con el intérprete del agente
    (así tiene disponibles openai, anthropic, requests, psutil, etc.)."""
    if not (codigo or "").strip():
        return {"error": "No mandaste código"}

    cwd, error = _cwd(directorio, base or Path.home())
    if error:
        return error

    bloqueo = _pedir_permiso(codigo, True, cwd, permisos)
    if bloqueo:
        return bloqueo

    with tempfile.TemporaryDirectory(prefix="ejec_py_") as tmp:
        script = Path(tmp) / "script.py"
        script.write_text(codigo, encoding="utf-8")
        try:
            from core import paquetes
            proc = subprocess.run(
                [interprete.interprete(), str(script)], cwd=str(cwd),
                capture_output=True, text=True, timeout=_timeout(timeout),
                env=paquetes.entorno(base),
            )
        except subprocess.TimeoutExpired:
            return {"error": f"El script excedió los {_timeout(timeout)}s y se cortó"}
        except Exception as e:
            return {"error": f"No pude ejecutar el script: {e}"}

    return _resultado(proc, {"directorio": str(cwd), "lineas_codigo": len(codigo.splitlines())})


# --- Tools -----------------------------------------------------------------

def ejecutar_tool_ejecucion(nombre: str, argumentos: dict,
                            base: Optional[Path] = None,
                            permisos: Optional[set] = None,
                            admin_habilitado: bool = False) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "ejecutar_shell":
        return ejecutar_shell(a.get("comando", ""), a.get("directorio", ""),
                              a.get("timeout", TIMEOUT_DEFAULT), base, permisos,
                              como_admin=bool(a.get("como_admin")),
                              admin_habilitado=admin_habilitado)
    if nombre == "ejecutar_python":
        return ejecutar_python(a.get("codigo", ""), a.get("directorio", ""),
                               a.get("timeout", TIMEOUT_DEFAULT), base, permisos)
    return {"error": f"Herramienta de ejecución desconocida: {nombre}"}


TOOLS_SCHEMA_EJECUCION: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ejecutar_shell",
            "description": (
                f"{plataforma.descripcion_shell()} Podés trabajar sobre "
                "cualquier ruta del sistema. Todo lo que borre o sobrescriba archivos "
                "se le va a preguntar al usuario antes "
                "de correr, así que no evites esas operaciones: pedilas normalmente y "
                "esperá la respuesta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "comando": {"type": "string", "description": f"El comando completo, {plataforma.sintaxis_shell()}"},
                    "directorio": {"type": "string", "description": "Directorio de trabajo. Default: el HOME del usuario"},
                    "timeout": {"type": "integer", "description": f"Segundos antes de cortar. Default {TIMEOUT_DEFAULT}, máximo {TIMEOUT_MAXIMO}"}
                },
                "required": ["comando"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_python",
            "description": (
                "Ejecuta un script Python en un proceso aparte, con el intérprete del "
                "agente (tiene openai, anthropic, requests, psutil, dotenv instalados). "
                "Usalo para procesar archivos, parsear datos o cualquier cosa que sea "
                "más clara en Python que en shell. Lo que imprimas con print() vuelve "
                "como stdout. Igual que en shell, borrar archivos requiere confirmación "
                "del usuario."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "codigo": {"type": "string", "description": "El script Python completo"},
                    "directorio": {"type": "string", "description": "Directorio de trabajo. Default: el HOME del usuario"},
                    "timeout": {"type": "integer", "description": f"Segundos antes de cortar. Default {TIMEOUT_DEFAULT}, máximo {TIMEOUT_MAXIMO}"}
                },
                "required": ["codigo"]
            }
        }
    }
]
