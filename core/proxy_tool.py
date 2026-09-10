"""
Tool del chat para traer el contexto de un flujo del proxy.

Es el puente entre el proxy (tráfico capturado) y la conversación: en el chat
referenciás un flujo por id ('@flujo 983') y el agente llama a esta tool, que
resuelve el id contra la base del proxy y devuelve el contexto REDACTADO
(headers sin cookies de sesión ni tokens; el body del flujo bajo análisis sí,
que es lo que se quiere ver).

## Dos cosas que este módulo NO hace, a propósito

**No entrega la ruta de la base de datos.** Antes la devolvía en cada búsqueda
y la descripción de la tool le sugería al modelo "procesá todo en bloque con
ejecutar_python leyendo la tabla flujos". Eso esquivaba por completo la
redacción de core/marcas.py: en la tabla los headers están completos, con las
cookies de sesión y el Authorization, y de ahí salían derecho al proveedor del
LLM. La lectura en bloque ahora se hace con `extraer_de_captura`, que devuelve
los cuerpos y ningún header.

**No matchea hosts por subcadena.** El filtro era `host LIKE '%sitio%'`, que
con sitio='google.com' también traía 'google.com.ar.phish.net'. Ahora la
comparación pasa por core/dominios.py, que usa la misma regla que el filtro de
captura (igualdad o subdominio con punto) y la Public Suffix List. Cuando el
nombre es ambiguo se devuelven los candidatos en vez de elegir uno: adivinar
mal en silencio es peor que preguntar.

El agente es un planner: recibe el contexto y razona. Ojo con la conclusión
fácil de que por eso no hay riesgo — esta tool solo lee, pero el mismo modelo
que lee tiene ejecutar_shell en el turno siguiente. Lo capturado es contenido
no confiable y el prompt del sistema lo dice.
"""

from typing import Any, Dict, List, Optional

# Cuánto texto de cada cuerpo se devuelve en una lectura en bloque. El tope
# total existe para no volcar 300 respuestas enteras en el contexto.
MAX_CHARS_POR_CUERPO = 4_000
MAX_CHARS_TOTAL = 120_000


def _db():
    from core.proxy import dir_proxy
    return dir_proxy() / "sesion.db"


def _con(db):
    import sqlite3
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _hosts_capturados(con) -> List[str]:
    return [r["host"] for r in
            con.execute("SELECT DISTINCT host FROM flujos WHERE host <> ''")]


def _resolver_sitio(con, sitio: Optional[str]) -> Dict[str, Any]:
    """Traduce lo que pidió el modelo a hosts concretos de la captura.

    Devuelve {'hosts': [...]} para filtrar, o {'aviso': ...} con los candidatos
    cuando no se puede decidir sin preguntar."""
    from core import dominios
    if not sitio:
        return {"hosts": None}          # sin filtro de sitio

    r = dominios.resolver(_hosts_capturados(con), sitio)
    if "sitio" in r:
        return {"hosts": r["hosts"], "sitio": r["sitio"]}

    if r.get("candidatos"):
        return {"aviso": (
            f"'{sitio}' coincide con más de un sitio capturado. No elijo por vos: "
            f"preguntale a la persona cuál de estos quiere y volvé a llamarme con "
            f"el dominio completo."),
            "candidatos": r["candidatos"]}

    # 'candidatos' va siempre, aunque esté vacío: así el que consume esto tiene
    # una sola forma que mirar en vez de dos.
    return {"aviso": (f"No hay nada capturado de '{sitio}'. Estos son los sitios "
                      f"que sí hay; preguntale a la persona si se refería a alguno."),
            "candidatos": [],
            "sitios": r.get("sitios", [])}


def listar_sitios_capturados() -> Dict[str, Any]:
    """Qué sitios navegó la persona (para inferir de cuál habla sin que lo marque)."""
    from core import dominios
    db = _db()
    if not db.exists():
        return {"sitios": [], "aviso": "Todavía no hay nada capturado. Abrí Firefox "
                "de captura y navegá el sitio primero."}
    con = _con(db)
    filas = con.execute(
        "SELECT host, COUNT(*) AS flujos, MAX(ts) AS ultima "
        "FROM flujos WHERE host <> '' GROUP BY host ORDER BY flujos DESC").fetchall()
    con.close()

    # Se agrupa por dominio registrable: al modelo le sirve más "linkedin.com
    # (3 hosts, 412 flujos)" que ver www., static. y api. como sitios distintos.
    por_sitio: Dict[str, Dict[str, Any]] = {}
    for r in filas:
        d = dominios.registrable(r["host"])
        e = por_sitio.setdefault(d, {"sitio": d, "hosts": [], "flujos": 0, "ultima": 0})
        e["hosts"].append(r["host"])
        e["flujos"] += r["flujos"]
        e["ultima"] = max(e["ultima"], r["ultima"] or 0)

    orden = sorted(por_sitio.values(), key=lambda e: e["flujos"], reverse=True)
    return {"sitios": orden}


def buscar_en_captura(sitio: str = None, texto: str = None, tipo: str = None,
                      metodo: str = None, limite: int = 40) -> Dict[str, Any]:
    """Lista los flujos capturados que matchean. Devuelve metadata, no cuerpos:
    con los ids después leés uno de muestra (ver_cuerpo_flujo) o los procesás
    todos con extraer_de_captura."""
    db = _db()
    if not db.exists():
        return {"flujos": [], "aviso": "No hay captura todavía."}

    con = _con(db)
    resuelto = _resolver_sitio(con, sitio)
    if "aviso" in resuelto:
        con.close()
        return resuelto

    cond, args = ["host <> ''"], []
    if resuelto["hosts"] is not None:
        # Lista explícita de hosts, no LIKE: es lo que evita arrastrar dominios
        # parecidos o lookalikes.
        marcas_sql = ",".join("?" for _ in resuelto["hosts"])
        cond.append(f"host IN ({marcas_sql})")
        args += resuelto["hosts"]
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

    filas = con.execute(
        "SELECT id, ts, metodo, host, ruta, query, estado, resp_tipo, "
        "length(resp_body) AS resp_len FROM flujos WHERE " + " AND ".join(cond) +
        " ORDER BY ts DESC LIMIT ?", (*args, lim)).fetchall()
    con.close()
    return {
        "total": len(filas),
        "sitio": resuelto.get("sitio"),
        "flujos": [{"id": r["id"], "metodo": r["metodo"], "sitio": r["host"],
                    "ruta": r["ruta"], "query": (r["query"] or "")[:200],
                    "estado": r["estado"], "tipo": r["resp_tipo"],
                    "bytes": r["resp_len"], "ts": r["ts"]} for r in filas],
    }


def extraer_de_captura(sitio: str = None, texto: str = None, tipo: str = None,
                       metodo: str = None, limite: int = 30,
                       max_chars: int = MAX_CHARS_POR_CUERPO) -> Dict[str, Any]:
    """Cuerpos de muchos flujos de una sola vez, para escribir la extracción.

    Reemplaza al viejo camino de "leé la SQLite con ejecutar_python": da la
    misma potencia (procesar decenas de respuestas en una llamada) sin exponer
    los headers, que es donde viven las cookies de sesión.
    """
    from core.proxy import cuerpo_legible
    db = _db()
    if not db.exists():
        return {"flujos": [], "aviso": "No hay captura todavía."}

    con = _con(db)
    resuelto = _resolver_sitio(con, sitio)
    if "aviso" in resuelto:
        con.close()
        return resuelto

    cond, args = ["host <> ''"], []
    if resuelto["hosts"] is not None:
        marcas_sql = ",".join("?" for _ in resuelto["hosts"])
        cond.append(f"host IN ({marcas_sql})")
        args += resuelto["hosts"]
    if texto:
        cond.append("(ruta LIKE ? OR query LIKE ?)"); args += [f"%{texto}%", f"%{texto}%"]
    if tipo:
        cond.append("resp_tipo LIKE ?"); args.append(f"%{tipo}%")
    if metodo:
        cond.append("metodo = ?"); args.append(metodo.upper())

    try:
        lim = max(1, min(int(limite), 100))
    except (TypeError, ValueError):
        lim = 30
    try:
        cap = max(200, min(int(max_chars), 20_000))
    except (TypeError, ValueError):
        cap = MAX_CHARS_POR_CUERPO

    filas = con.execute(
        "SELECT id, metodo, host, ruta, query, estado, resp_tipo, req_body, resp_body "
        "FROM flujos WHERE " + " AND ".join(cond) +
        " ORDER BY ts DESC LIMIT ?", (*args, lim)).fetchall()
    con.close()

    salida, usado, truncados = [], 0, 0
    for r in filas:
        if usado >= MAX_CHARS_TOTAL:
            truncados += 1
            continue
        cuerpo = cuerpo_legible(r["resp_body"], r["resp_tipo"] or "")
        recorte = cuerpo["texto"][:cap]
        usado += len(recorte)
        salida.append({
            "id": r["id"], "metodo": r["metodo"], "sitio": r["host"],
            "ruta": r["ruta"], "query": (r["query"] or "")[:200],
            "estado": r["estado"], "tipo": r["resp_tipo"],
            "binario": cuerpo["binario"],
            "resp_body": recorte + ("… (truncado)" if len(cuerpo["texto"]) > cap else ""),
        })

    resultado = {"total": len(salida), "sitio": resuelto.get("sitio"), "flujos": salida}
    if truncados:
        resultado["aviso"] = (f"Corté en {MAX_CHARS_TOTAL} caracteres: quedaron "
                              f"{truncados} flujos sin traer. Afiná los filtros o "
                              f"bajá max_chars y volvé a pedir.")
    return resultado


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
    if nombre == "extraer_de_captura":
        return extraer_de_captura(a.get("sitio"), a.get("texto"), a.get("tipo"),
                                  a.get("metodo"), a.get("limite", 30),
                                  a.get("max_chars", MAX_CHARS_POR_CUERPO))
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
                "Qué sitios navegó la persona, agrupados por dominio (con sus "
                "hosts, cuántos flujos y la última vez). Úsalo PRIMERO cuando te "
                "hable de un sitio por su nombre ('buscá en Google', 'los "
                "trabajos de LinkedIn'). Si el nombre da para más de un sitio, "
                "mostrale la lista y preguntale cuál: NO elijas por tu cuenta, "
                "porque trabajar sobre el dominio equivocado pasa inadvertido."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_captura",
            "description": (
                "Busca flujos capturados por sitio, texto en la ruta o query, "
                "content-type o método. Devuelve metadata (id, método, ruta, "
                "status, tipo, bytes), no los cuerpos. En 'sitio' pasá el dominio "
                "(ej: 'linkedin.com'); se matchea el dominio y sus subdominios, no "
                "por subcadena. Si el nombre es ambiguo te devuelvo los candidatos "
                "para que le preguntes a la persona. Con los ids leés uno de "
                "muestra con ver_cuerpo_flujo, o traés todos los cuerpos juntos "
                "con extraer_de_captura."),
            "parameters": {
                "type": "object",
                "properties": {
                    "sitio": {"type": "string", "description": "dominio (ej: 'linkedin.com')"},
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
            "name": "extraer_de_captura",
            "description": (
                "Los CUERPOS de varios flujos capturados de una sola llamada, "
                "para procesarlos en bloque y escribir la extracción. Mismos "
                "filtros que buscar_en_captura. Es el camino para trabajar sobre "
                "muchas respuestas a la vez; no intentes leer la base de datos "
                "del proxy por tu cuenta. No devuelve headers: si necesitás "
                "razonar sobre autenticación, pedile a la persona que active la "
                "opción de incluir los valores de sesión."),
            "parameters": {
                "type": "object",
                "properties": {
                    "sitio": {"type": "string", "description": "dominio (ej: 'linkedin.com')"},
                    "texto": {"type": "string", "description": "texto en la ruta o query"},
                    "tipo": {"type": "string", "description": "content-type (ej: 'json')"},
                    "metodo": {"type": "string", "description": "GET, POST, …"},
                    "limite": {"type": "integer", "description": "máx flujos (default 30, tope 100)"},
                    "max_chars": {"type": "integer",
                                  "description": f"máx caracteres por cuerpo (default {MAX_CHARS_POR_CUERPO})"},
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
