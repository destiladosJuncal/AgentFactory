"""
Conectores — integraciones "oficiales" con servicios externos (Grafana, Gmail,
Drive, HubSpot…) que el agente puede usar como herramientas.

Cada conector es un módulo con el MISMO contrato que el resto de las tools del
chat (patrón proxy_tool/tarea_tool), más un poco de metadata para la UI de
Config:

    ID: str                      # slug, prefijo de sus tools ("grafana")
    NOMBRE: str                  # nombre lindo ("Grafana")
    esta_configurado() -> bool   # ¿hay credenciales cargadas?
    probar() -> {ok, detalle|error}
    TOOLS_SCHEMA: list           # schemas OpenAI-style de sus tools
    ejecutar(nombre, args) -> dict

El registro de acá:
  · publica al modelo SOLO los conectores configurados (si no cargaste Grafana,
    sus tools ni aparecen — no ensucian el prompt),
  · enruta el despacho por nombre de tool,
  · da estado para la pantalla de Config.

Las ESCRITURAS de cada conector (mandar, crear, actualizar) pasan por el guard
de confirmación compartido (`solicitar_confirmacion`), el mismo modal que ya
aprueba los comandos de shell.

MCP entra acá como UN conector más (core/conectores/mcp.py): introspecta un
server externo y publica sus tools con este mismo contrato.
"""

from typing import Any, Dict, List, Set

from core.conectores import grafana as _grafana

# Conectores nativos registrados. Sumar uno nuevo = agregarlo a esta lista.
NATIVOS = [_grafana]


def _modulo_de(nombre_tool: str):
    for c in NATIVOS:
        if any(t["function"]["name"] == nombre_tool for t in c.TOOLS_SCHEMA):
            return c
    return None


def activos() -> List[Any]:
    """Conectores con credenciales cargadas (los únicos que ve el modelo)."""
    return [c for c in NATIVOS if _seguro(c.esta_configurado, False)]


def esquema_conectores() -> List[Dict[str, Any]]:
    """Schemas de las tools de los conectores configurados (para pasar al modelo)."""
    out: List[Dict[str, Any]] = []
    for c in activos():
        out.extend(c.TOOLS_SCHEMA)
    return out


def nombres_conectores() -> Set[str]:
    """Nombres de tool de los conectores configurados."""
    return {t["function"]["name"] for c in activos() for t in c.TOOLS_SCHEMA}


def ejecutar_tool_conector(nombre: str, argumentos: dict) -> Dict[str, Any]:
    """Despacha una tool de conector. No levanta: ante error devuelve {error}."""
    c = _modulo_de(nombre)
    if c is None:
        return {"error": f"No hay conector para la herramienta '{nombre}'."}
    try:
        return c.ejecutar(nombre, argumentos or {})
    except Exception as e:  # defensivo: el chat nunca debe cortarse por esto
        return {"error": f"El conector '{getattr(c, 'ID', '?')}' falló "
                         f"({type(e).__name__}: {e})."}


# -- para la UI de Config ----------------------------------------------------

def estado_todos() -> List[Dict[str, Any]]:
    """Lista de conectores con su estado, para pintar las cards de Config."""
    return [{"id": c.ID, "nombre": c.NOMBRE,
             "configurado": _seguro(c.esta_configurado, False)}
            for c in NATIVOS]


def probar(cid: str) -> Dict[str, Any]:
    """Prueba la conexión de un conector por su id."""
    for c in NATIVOS:
        if c.ID == cid:
            try:
                return c.probar()
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": "conector desconocido"}


# -- guard de confirmación compartido ----------------------------------------

def solicitar_confirmacion(resumen: str, detalle: str = "") -> bool:
    """True si la persona autoriza una ESCRITURA del conector. Reusa el mismo
    guard que el resto de las operaciones que no se pueden deshacer: si nadie
    registró un confirmador, se RECHAZA (no romper nada por defecto)."""
    from core import ejecucion
    fn = ejecucion.CONFIRMADOR
    if fn is None:
        return False
    try:
        return fn(resumen, detalle, "conector") in ("permitir", "siempre")
    except Exception:
        return False


def _seguro(fn, default):
    try:
        return fn()
    except Exception:
        return default
