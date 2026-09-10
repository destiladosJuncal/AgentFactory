"""
Tool del chat para traer el contexto de un flujo del proxy.

Es el puente entre el proxy (tráfico capturado) y la conversación: en el chat
referenciás un flujo por id ('@flujo 983') y el agente llama a esta tool, que
resuelve el id contra la base del proxy y devuelve el contexto REDACTADO
(headers sin cookies de sesión ni tokens; el body del flujo bajo análisis sí,
que es lo que se quiere ver).

El agente es un planner: recibe el contexto y razona. No ejecuta nada sobre él
—esta tool solo lee—, así que un response hostil capturado no puede componer
una acción peligrosa a través de acá.
"""

from typing import Any, Dict, List


def _db():
    from core.proxy import dir_proxy
    return dir_proxy() / "sesion.db"


def _con(db):
    import sqlite3
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def listar_sitios_capturados() -> Dict[str, Any]:
    """Qué sitios navegó la persona (para inferir de cuál habla sin que lo marque)."""
    db = _db()
    if not db.exists():
        return {"sitios": [], "aviso": "Todavía no hay nada capturado. Abrí Firefox "
                "de captura y navegá el sitio primero."}
    con = _con(db)
    filas = con.execute(
        "SELECT host, COUNT(*) AS flujos, MAX(ts) AS ultima "
        "FROM flujos WHERE host <> '' GROUP BY host ORDER BY flujos DESC").fetchall()
    con.close()
    return {"sitios": [{"sitio": r["host"], "flujos": r["flujos"], "ultima": r["ultima"]}
                       for r in filas],
            "db": str(db)}


def buscar_en_captura(sitio: str = None, texto: str = None, tipo: str = None,
                      metodo: str = None, limite: int = 40) -> Dict[str, Any]:
    """Lista los flujos capturados que matchean (por sitio/host, texto en la
    ruta o query, content-type, método). Devuelve metadata, no los bodies: con
    los ids después leés un cuerpo de muestra (ver_cuerpo_flujo) o los procesás
    en bloque con ejecutar_python leyendo la SQLite `db`."""
    db = _db()
    if not db.exists():
        return {"flujos": [], "aviso": "No hay captura todavía."}
    cond, args = ["host <> ''"], []
    if sitio:
        cond.append("host LIKE ?"); args.append(f"%{sitio}%")
    if texto:
        cond.append("(ruta LIKE ? OR query LIKE ?)"); args += [f"%{texto}%", f"%{texto}%"]
    if tipo:
        cond.append("resp_tipo LIKE ?"); args.append(f"%{tipo}%")
    if metodo:
        cond.append("metodo = ?"); args.append(metodo.upper())
    try:
        lim = max(1, min(int(limite), 200))
    except (TypeError, ValueError):
        lim = 40
    con = _con(db)
    filas = con.execute(
        "SELECT id, ts, metodo, host, ruta, query, estado, resp_tipo, "
        "length(resp_body) AS resp_len FROM flujos WHERE " + " AND ".join(cond) +
        " ORDER BY ts DESC LIMIT ?", (*args, lim)).fetchall()
    con.close()
    return {
        "total": len(filas),
        "db": str(db),
        "flujos": [{"id": r["id"], "metodo": r["metodo"], "sitio": r["host"],
                    "ruta": r["ruta"], "query": (r["query"] or "")[:200],
                    "estado": r["estado"], "tipo": r["resp_tipo"],
                    "bytes": r["resp_len"], "ts": r["ts"]} for r in filas],
    }


def ver_cuerpo_flujo(flujo_id: int, max_chars: int = 4000, redactar: bool = True) -> Dict[str, Any]:
    """Cuerpo (req y resp) decodificado de UN flujo, para ver la estructura antes
    de escribir la extracción. Headers/secretos redactados; el payload se ve."""
    ctx = contexto_de_flujo(flujo_id, ventana=0, redactar=redactar)
    if "error" in ctx:
        return ctx
    f = ctx.get("flujo") or ctx.get("ficha") or ctx
    try:
        cap = max(200, min(int(max_chars), 20000))
    except (TypeError, ValueError):
        cap = 4000

    def _corta(v):
        v = v or ""
        return v[:cap] + ("… (truncado)" if len(v) > cap else "")

    return {
        "id": f.get("id", flujo_id),
        "url": f.get("url"),
        "status": f.get("status"),
        "resp_content_type": f.get("resp_content_type") or f.get("resp_tipo"),
        "req_body": _corta(f.get("req_body")),
        "resp_body": _corta(f.get("resp_body")),
    }


def contexto_de_flujo(flujo_id: int, ventana: int = 5, redactar: bool = True) -> Dict[str, Any]:
    from core import marcas
    db = _db()
    if not db.exists():
        return {"error": "No hay ninguna sesión de proxy capturada todavía."}
    try:
        fid, v = int(flujo_id), int(ventana)
    except (TypeError, ValueError):
        return {"error": f"flujo_id inválido: {flujo_id!r}"}
    return marcas.contexto_flujo(db, fid, ventana=v, redactar=redactar)


def listar_marcados() -> Dict[str, Any]:
    from core import marcas
    db = _db()
    if not db.exists():
        return {"marcas": []}
    return {"marcas": marcas.listar_marcas(db)}


def ejecutar_tool_proxy(nombre: str, argumentos: dict, redactar: bool = True) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "contexto_flujo_proxy":
        return contexto_de_flujo(a.get("flujo_id"), a.get("ventana", 5), redactar=redactar)
    if nombre == "listar_flujos_marcados":
        return listar_marcados()
    if nombre == "listar_sitios_capturados":
        return listar_sitios_capturados()
    if nombre == "buscar_en_captura":
        return buscar_en_captura(a.get("sitio"), a.get("texto"), a.get("tipo"),
                                 a.get("metodo"), a.get("limite", 40))
    if nombre == "ver_cuerpo_flujo":
        return ver_cuerpo_flujo(a.get("flujo_id"), a.get("max_chars", 4000),
                                redactar=redactar)
    return {"error": f"Herramienta de proxy desconocida: {nombre}"}


TOOLS_SCHEMA_PROXY: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "contexto_flujo_proxy",
            "description": (
                "Trae el contexto de un flujo capturado por el proxy, por su id "
                "(cuando la persona escribe '@flujo 983' o 'el flujo 983'). "
                "Devuelve la ficha (método, URL, status, headers redactados, "
                "body), una ventana temporal de la misma sesión, el grupo del "
                "mismo endpoint (para ver si el status es anómalo) y las marcas "
                "puestas. Las cookies de sesión y tokens vienen enmascarados."),
            "parameters": {
                "type": "object",
                "properties": {
                    "flujo_id": {"type": "integer", "description": "id del flujo en el proxy"},
                    "ventana": {"type": "integer",
                                "description": "cuántos flujos antes/después traer (default 5)"},
                },
                "required": ["flujo_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_flujos_marcados",
            "description": ("Lista los flujos que la persona marcó en el proxy "
                            "(con su etiqueta, nota y URL), para retomarlos."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_sitios_capturados",
            "description": (
                "Qué sitios navegó la persona (host + cuántos flujos + última vez). "
                "Úsalo PRIMERO cuando te hable de un sitio por su nombre "
                "('buscá en Google', 'los trabajos de LinkedIn') para inferir el "
                "host real sin que lo marque. Si hay más de uno que encaja o "
                "ninguno, preguntale sobre qué sitio querés que trabaje."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_captura",
            "description": (
                "Busca flujos capturados por sitio/host, texto en la ruta o query, "
                "content-type o método. Devuelve metadata (id, método, ruta, "
                "status, tipo, bytes) y la ruta de la SQLite `db`, NO los bodies. "
                "Con esos ids leés una muestra (ver_cuerpo_flujo) o procesás todo "
                "en bloque con ejecutar_python leyendo la tabla `flujos` de esa db."),
            "parameters": {
                "type": "object",
                "properties": {
                    "sitio": {"type": "string", "description": "host o parte (ej: 'linkedin')"},
                    "texto": {"type": "string", "description": "texto en la ruta o query"},
                    "tipo": {"type": "string", "description": "content-type (ej: 'json', 'html')"},
                    "metodo": {"type": "string", "description": "GET, POST, …"},
                    "limite": {"type": "integer", "description": "máx resultados (default 40)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ver_cuerpo_flujo",
            "description": (
                "Cuerpo (request y response) decodificado de UN flujo por id, para "
                "entender la estructura antes de escribir la extracción. Headers y "
                "secretos redactados; el payload se ve. Truncado a max_chars."),
            "parameters": {
                "type": "object",
                "properties": {
                    "flujo_id": {"type": "integer", "description": "id del flujo"},
                    "max_chars": {"type": "integer", "description": "máx caracteres por body (default 4000)"},
                },
                "required": ["flujo_id"],
            },
        },
    },
]
