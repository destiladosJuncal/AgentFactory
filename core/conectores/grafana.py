"""
Conector Grafana (nativo) — el piloto que fija el patrón de los conectores.

Auth: un token de service-account (o API key) + la URL base de tu Grafana. Sin
OAuth. Se cargan en Config como GRAFANA_URL y GRAFANA_TOKEN (.env protegido).

Tools (prefijo `grafana_`):
  LECTURA
    grafana_buscar          /api/search        dashboards/folders
    grafana_dashboard       /api/dashboards/uid modelo completo de un dashboard
    grafana_datasources     /api/datasources   datasources disponibles
    grafana_query           /api/ds/query      corre una consulta a un datasource
  ESCRITURA (pasan por el guard de confirmación)
    grafana_crear_anotacion /api/annotations   deja una anotación
    grafana_guardar_dashboard /api/dashboards/db crea/actualiza un dashboard

El HTTP sale por un seam (`_HTTP`) para poder testear sin un Grafana real.
"""

import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

ID = "grafana"
NOMBRE = "Grafana"


# -- credenciales ------------------------------------------------------------

def _cfg():
    url = (os.getenv("GRAFANA_URL") or "").strip().rstrip("/")
    token = (os.getenv("GRAFANA_TOKEN") or "").strip()
    return url, token


def esta_configurado() -> bool:
    url, token = _cfg()
    return bool(url and token)


# -- HTTP (seam inyectable en tests) -----------------------------------------

def _http_real(method: str, url: str, headers: Dict[str, str],
               body: Optional[dict]):
    """(method, url, headers, body) -> (status:int, data). Default real con requests."""
    import requests
    r = requests.request(method, url, headers=headers, json=body, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"_texto": (r.text or "")[:2000]}
    return r.status_code, data


_HTTP = _http_real


def _llamar(method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
    url, token = _cfg()
    if not (url and token):
        return {"error": "Grafana no está conectado. Cargá URL y token en "
                         "⚙️ Config → Conectores."}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "Content-Type": "application/json"}
    try:
        status, data = _HTTP(method, url + path, headers, body)
    except Exception as e:
        return {"error": f"No pude hablar con Grafana ({type(e).__name__}: {e}). "
                         f"¿La URL es correcta y Grafana está accesible?"}
    if status in (401, 403):
        return {"error": f"Grafana rechazó la credencial (HTTP {status}). Revisá "
                         f"el token y sus permisos."}
    if status >= 400:
        msg = data.get("message") if isinstance(data, dict) else None
        return {"error": f"Grafana respondió HTTP {status}" + (f": {msg}" if msg else ".")}
    return {"ok": True, "data": data}


def probar() -> Dict[str, Any]:
    if not esta_configurado():
        return {"ok": False, "error": "Falta la URL o el token."}
    r = _llamar("GET", "/api/org")
    if r.get("error"):
        return {"ok": False, "error": r["error"]}
    data = r.get("data") or {}
    org = data.get("name") if isinstance(data, dict) else None
    return {"ok": True, "detalle": f"Conectado a «{org}»." if org else "Conexión OK."}


# -- lectura -----------------------------------------------------------------

def buscar(query: str = "", tipo: str = "", limite: int = 20) -> Dict[str, Any]:
    path = f"/api/search?limit={max(1, min(int(limite or 20), 1000))}"
    if query:
        path += "&query=" + quote(str(query))
    if tipo:
        path += "&type=" + quote(str(tipo))
    return _llamar("GET", path)


def dashboard(uid: str) -> Dict[str, Any]:
    if not uid:
        return {"error": "Falta 'uid' (lo da grafana_buscar)."}
    return _llamar("GET", f"/api/dashboards/uid/{quote(str(uid))}")


def datasources() -> Dict[str, Any]:
    return _llamar("GET", "/api/datasources")


def query(payload: dict) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not payload.get("queries"):
        return {"error": "'payload' debe ser el cuerpo de /api/ds/query de Grafana: "
                         "un objeto con 'queries' (cada una con refId y "
                         "datasource.uid). Usá grafana_datasources para el uid y "
                         "grafana_dashboard para ver cómo están armadas las queries."}
    return _llamar("POST", "/api/ds/query", payload)


# -- escritura (con guard de confirmación) -----------------------------------

def _confirmar(resumen: str, detalle: str) -> bool:
    from core import conectores
    return conectores.solicitar_confirmacion(resumen, detalle)


def crear_anotacion(text: str, tags: Optional[list] = None,
                    dashboard_uid: str = "", panel_id: Optional[int] = None,
                    time_ms: Optional[int] = None,
                    time_end_ms: Optional[int] = None) -> Dict[str, Any]:
    if not text:
        return {"error": "Falta 'text' (el contenido de la anotación)."}
    cuerpo: Dict[str, Any] = {"text": str(text)}
    if tags:
        cuerpo["tags"] = list(tags)
    if dashboard_uid:
        cuerpo["dashboardUID"] = str(dashboard_uid)
    if panel_id is not None:
        cuerpo["panelId"] = int(panel_id)
    if time_ms is not None:
        cuerpo["time"] = int(time_ms)
    if time_end_ms is not None:
        cuerpo["timeEnd"] = int(time_end_ms)
    if not _confirmar("crear una anotación en Grafana",
                      f"text={text!r}\ntags={tags}\ndashboardUID={dashboard_uid or '—'}"):
        return {"error": "Cancelado: no se creó la anotación (falta tu confirmación)."}
    return _llamar("POST", "/api/annotations", cuerpo)


def guardar_dashboard(dashboard: dict, folder_uid: str = "",
                      overwrite: bool = False, mensaje: str = "") -> Dict[str, Any]:
    if not isinstance(dashboard, dict) or not dashboard:
        return {"error": "'dashboard' debe ser el modelo JSON del dashboard "
                         "(como el que devuelve grafana_dashboard, en .dashboard)."}
    cuerpo: Dict[str, Any] = {"dashboard": dashboard, "overwrite": bool(overwrite)}
    if folder_uid:
        cuerpo["folderUid"] = str(folder_uid)
    if mensaje:
        cuerpo["message"] = str(mensaje)
    titulo = dashboard.get("title", "(sin título)")
    if not _confirmar("crear/actualizar un dashboard en Grafana",
                      f"título={titulo!r}\noverwrite={overwrite}\nfolderUid={folder_uid or '—'}"):
        return {"error": "Cancelado: no se guardó el dashboard (falta tu confirmación)."}
    return _llamar("POST", "/api/dashboards/db", cuerpo)


# -- despacho ----------------------------------------------------------------

def ejecutar(nombre: str, argumentos: dict) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "grafana_buscar":
        return buscar(a.get("query", ""), a.get("tipo", ""), a.get("limite", 20))
    if nombre == "grafana_dashboard":
        return dashboard(a.get("uid", ""))
    if nombre == "grafana_datasources":
        return datasources()
    if nombre == "grafana_query":
        return query(a.get("payload"))
    if nombre == "grafana_crear_anotacion":
        return crear_anotacion(a.get("text", ""), a.get("tags"), a.get("dashboard_uid", ""),
                               a.get("panel_id"), a.get("time_ms"), a.get("time_end_ms"))
    if nombre == "grafana_guardar_dashboard":
        return guardar_dashboard(a.get("dashboard"), a.get("folder_uid", ""),
                                 bool(a.get("overwrite", False)), a.get("mensaje", ""))
    return {"error": f"Tool de Grafana desconocida: {nombre}"}


TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "grafana_buscar",
            "description": ("Busca dashboards y carpetas en Grafana (GET /api/search). "
                            "Devuelve título, uid, url y tipo. Punto de entrada para "
                            "encontrar el uid de un dashboard."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "texto a buscar (vacío = todo)"},
                    "tipo": {"type": "string", "description": "'dash-db' para dashboards, 'dash-folder' para carpetas"},
                    "limite": {"type": "integer", "description": "máximo de resultados (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grafana_dashboard",
            "description": "Trae el modelo JSON completo de un dashboard por su uid (GET /api/dashboards/uid/:uid).",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string", "description": "uid del dashboard (de grafana_buscar)"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grafana_datasources",
            "description": "Lista los datasources de Grafana con su uid, nombre y tipo (GET /api/datasources). Necesario para armar queries.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grafana_query",
            "description": ("Corre una consulta contra un datasource (POST /api/ds/query). "
                            "'payload' es el cuerpo tal cual lo pide Grafana: "
                            "{'queries': [{'refId':'A','datasource':{'uid':'...'}, ...}], "
                            "'from':'now-1h','to':'now'}. Conseguí el uid con "
                            "grafana_datasources."),
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "cuerpo de /api/ds/query (con 'queries')"},
                },
                "required": ["payload"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grafana_crear_anotacion",
            "description": ("ESCRIBE: deja una anotación en Grafana (POST /api/annotations). "
                            "Requiere confirmación de la persona. Útil para marcar un deploy, "
                            "un incidente o un hito en los dashboards."),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "contenido de la anotación"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "etiquetas"},
                    "dashboard_uid": {"type": "string", "description": "uid del dashboard (opcional; vacío = global)"},
                    "panel_id": {"type": "integer", "description": "id del panel (opcional)"},
                    "time_ms": {"type": "integer", "description": "epoch en ms (opcional; default = ahora)"},
                    "time_end_ms": {"type": "integer", "description": "epoch fin en ms para anotación de región (opcional)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grafana_guardar_dashboard",
            "description": ("ESCRIBE: crea o actualiza un dashboard (POST /api/dashboards/db). "
                            "Requiere confirmación. 'dashboard' es el modelo JSON (como el de "
                            "grafana_dashboard, campo .dashboard). Para actualizar uno existente, "
                            "conservá su uid y pasá overwrite=true."),
            "parameters": {
                "type": "object",
                "properties": {
                    "dashboard": {"type": "object", "description": "modelo JSON del dashboard"},
                    "folder_uid": {"type": "string", "description": "uid de la carpeta destino (opcional)"},
                    "overwrite": {"type": "boolean", "description": "sobreescribir si ya existe (default false)"},
                    "mensaje": {"type": "string", "description": "mensaje de cambio para el historial de versiones"},
                },
                "required": ["dashboard"],
            },
        },
    },
]
