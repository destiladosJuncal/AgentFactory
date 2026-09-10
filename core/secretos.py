"""
Cifrado en reposo de los headers de la captura.

## Qué problema resuelve, y cuál no

La base de la captura guardaba los headers en texto plano, con las cookies de
sesión y el `Authorization` completos. Cualquier script podía deducir la ruta
—el proceso hijo hereda `AGENTE_DATOS`— hacer un `SELECT` y llevarse las
credenciales al prompt, y de ahí al proveedor del modelo.

Con esto, ese `SELECT` devuelve bytes ilegibles. La exposición **accidental**
desaparece: ya no alcanza con leer la tabla.

Lo que NO resuelve, dicho de frente: el agente ejecuta código como el mismo
usuario, así que puede llegar al archivo de clave igual que la app. La defensa
no es criptográfica, es de **consentimiento**: descifrar pasa por el mismo
diálogo que un comando destructivo, con "permitir una vez" o "permitir siempre"
por conversación. El objetivo es que acceder a credenciales sea un acto que se
ve y se aprueba, no un efecto secundario de procesar la captura.

Para el caso del disco robado la herramienta correcta es BitLocker o FileVault,
no esto.

## Detalles

  · Fernet (AES-128-CBC + HMAC), que viene con `cryptography`, ya dependencia
    de mitmproxy. No se agrega nada nuevo al arranque portable.
  · La clave vive con los DATOS, nunca con el código, así que un zip para
    compartir no se la lleva — igual que el `.env`.
  · Los valores que ya estaban en claro se leen igual: `descifrar` los devuelve
    tal cual si no tienen el prefijo. Así una captura vieja no se rompe y no
    hace falta migrar nada de entrada.
"""

import base64
from pathlib import Path
from typing import Optional

# Marca al principio del valor cifrado. Sirve para dos cosas: distinguir lo
# cifrado de lo que quedó en claro de antes, y que un humano que abra la base
# entienda qué está viendo en vez de pensar que se corrompió.
PREFIJO = "enc:v1:"

NOMBRE_CLAVE = "clave-captura.key"

_cache = {}


def ruta_clave() -> Path:
    from core.rutas import dir_datos
    return dir_datos() / NOMBRE_CLAVE


def _proteger(ruta: Path):
    """Permisos restrictivos donde el sistema los respeta."""
    from core.rutas import proteger
    proteger(ruta)


def clave() -> Optional[bytes]:
    """La clave de esta instalación. Se crea la primera vez que se usa.

    Devuelve None si no se puede tener (falta `cryptography`, o la carpeta de
    datos no es escribible). En ese caso el resto degrada a texto plano en vez
    de perder la captura: es una decisión deliberada — que la app siga
    funcionando importa más que cifrar a toda costa, y el estado real se ve en
    el panel de Diagnóstico."""
    ruta = ruta_clave()
    en_cache = _cache.get("clave")
    if en_cache is not None and _cache.get("ruta") == str(ruta):
        return en_cache

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None

    try:
        if ruta.exists():
            material = ruta.read_bytes().strip()
        else:
            material = Fernet.generate_key()
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(material)
            _proteger(ruta)
    except OSError:
        return None

    # Una clave corrupta se rehace: perder la captura vieja es malo, pero
    # dejar la app inutilizable es peor, y sin clave válida no se puede hacer
    # nada con lo que ya está guardado.
    try:
        Fernet(material)
    except Exception:
        try:
            material = Fernet.generate_key()
            ruta.write_bytes(material)
            _proteger(ruta)
        except OSError:
            return None

    _cache["clave"] = material
    _cache["ruta"] = str(ruta)
    return material


def disponible() -> bool:
    return clave() is not None


def olvidar():
    """Limpia el cache. Para los tests y para cuando cambia AGENTE_DATOS."""
    _cache.clear()


def cifrar(texto: str) -> str:
    """Devuelve el texto cifrado con el prefijo, o tal cual si no se puede."""
    if not texto:
        return texto or ""
    material = clave()
    if material is None:
        return texto
    try:
        from cryptography.fernet import Fernet
        token = Fernet(material).encrypt(texto.encode("utf-8"))
        return PREFIJO + base64.b64encode(token).decode("ascii")
    except Exception:
        return texto


def esta_cifrado(valor) -> bool:
    return isinstance(valor, str) and valor.startswith(PREFIJO)


def descifrar(valor: str) -> str:
    """Descifra si hace falta. Lo que no está cifrado vuelve igual.

    OJO: esta función no pregunta nada. El consentimiento se pide en el borde
    —donde el agente pide contenido sin redactar, o donde un script referencia
    el almacén— porque acá abajo pasan también los usos internos (la huella de
    sesión, el agrupado de flujos, la pestaña del proxy) que no le muestran el
    valor a nadie y que no tendrían por qué molestar a la persona."""
    if not esta_cifrado(valor):
        return valor
    material = clave()
    if material is None:
        return ""
    try:
        from cryptography.fernet import Fernet
        crudo = base64.b64decode(valor[len(PREFIJO):].encode("ascii"))
        return Fernet(material).decrypt(crudo).decode("utf-8")
    except Exception:
        # Clave cambiada o dato corrupto: se devuelve vacío y no se revienta.
        # Un header ilegible degrada el análisis; una excepción acá rompería la
        # pestaña del proxy y el agrupado de sesiones.
        return ""


def estado() -> dict:
    """Para el panel de Diagnóstico."""
    ruta = ruta_clave()
    from core import plataforma
    permisos = None
    if ruta.exists():
        try:
            permisos = oct(ruta.stat().st_mode & 0o777)
        except OSError:
            pass
    return {
        "cifrado": disponible(),
        "clave": str(ruta),
        "clave_existe": ruta.exists(),
        "clave_permisos": permisos,
        "permisos_aplican": plataforma.soporta_permisos_posix(),
    }
