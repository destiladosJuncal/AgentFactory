"""
Matcher de value-flow: convierte tráfico capturado en candidatos rankeados.

NO es taint tracking (no instrumentamos el servidor). Es correlación black-box:
un valor que entra por una request y reaparece —igual, decodificado o
transformado— en otra parte. Señal más débil que el dataflow real, así que todo
el diseño gira alrededor de distinguir señal de ruido.

El pipeline es una cadena de scorers SEPARABLES, cada uno con su propio perfil
de falsos positivos, para poder medir cuál mete ruido y ajustarlo aislado:

  1. expandir_derivadas   ¿X se relaciona con Y? (encodings + hashes)
  2. score_cruce_contexto ¿las dos ocurrencias cruzan un parser distinto?
  3. score_material_sesion ¿es carried-credential o dato de negocio?
  4. score_entropia        ¿la colisión significa algo, o es id=2?
  5. ownership             ¿una respuesta devuelve ids de OTRA sesión? (IDOR)

El score de interés es la combinación pesada. El LLM nunca rankea: recibe el
top-N ya filtrado y hace la parte creativa (nombrar el límite, proponer el test).

CONTRATO DE ENTRADA — lo que produce la captura normalizada:

    mensaje = {
        "id": int,                 # id estable, ordena la captura
        "session": str,            # fingerprint de sesión (hash del material de
                                   #   auth); dos mensajes con el mismo = mismo rol
        "ts": float,               # epoch, para la ventana causal
        "method": str,             # "GET" | "POST" | ...
        "url": str,                # URL completa
        "status": int,             # código de respuesta
        "req_content_type": str,   # content-type de la request ("" si no hay)
        "resp_content_type": str,  # content-type de la respuesta
        "req_headers": [[k, v]],   # headers de request (incluye Cookie)
        "resp_headers": [[k, v]],  # headers de respuesta (incluye Set-Cookie)
        "req_body": str,           # cuerpo de request (texto; binario -> "")
        "resp_body": str,          # cuerpo de respuesta (texto; binario -> "")
        "auth_material": [str],    # OPCIONAL: valores que son auth (token/cookie).
                                   #   Es un HINT: el clasificador igual los detecta
                                   #   por frecuencia si no los pasás.
    }

`validar_mensajes` te dice si tu captura está bien formada antes de correr nada.
"""

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urlsplit


# --- Tipos internos ---------------------------------------------------------

@dataclass
class Ocurrencia:
    """Un valor visto en un lugar de un mensaje. Es la unidad atómica: los
    scorers razonan sobre ocurrencias, no sobre mensajes."""
    mensaje_id: int
    session: str
    ts: float
    lado: str          # 'request' | 'response'
    ubicacion: str     # 'url_path' | 'url_query' | 'header' | 'cookie' | 'body'
    clave: str         # nombre del param / header / json-path
    valor: str
    contexto: str      # el parser que gobierna esta ocurrencia: content-type,
                       #   'url' o 'header'. Lo lee el detector de cruce.


@dataclass
class Signal:
    """Lo que devuelve cada scorer: un número 0..1, por qué, y datos crudos.
    Uniforme a propósito, para que el combinador los junte sin casos especiales."""
    score: float
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidato:
    """Una hipótesis comprobable. Es lo que sube al LLM (el top-N)."""
    tipo: str                       # 'value_flow' | 'ownership'
    origen: Optional[Ocurrencia]    # source (más temprano); None en ownership
    destino: Ocurrencia             # sink
    transform: str                  # 'identity' | 'sha256' | 'base64.json.role' | ...
    interes: float = 0.0            # score combinado, para ordenar
    senales: Dict[str, Signal] = field(default_factory=dict)


# --- Validación de la entrada -----------------------------------------------

_REQUERIDOS = ("id", "session", "ts", "method", "url", "status")


def validar_mensajes(mensajes: List[dict]) -> List[str]:
    """Devuelve la lista de problemas (vacía = captura bien formada)."""
    problemas: List[str] = []
    vistos = set()
    for i, m in enumerate(mensajes):
        for campo in _REQUERIDOS:
            if campo not in m:
                problemas.append(f"mensaje #{i}: falta '{campo}'")
        mid = m.get("id")
        if mid in vistos:
            problemas.append(f"mensaje #{i}: id {mid} duplicado")
        vistos.add(mid)
        if not str(m.get("session", "")).strip():
            problemas.append(f"mensaje #{i}: 'session' vacío (todo caería en un solo rol)")
    return problemas


# --- Extracción de ocurrencias ----------------------------------------------

MAX_VALOR = 4096          # valores más largos no se indexan como tal
MIN_VALOR = 2             # descarta ruido de 1 carácter


def _walk_json(obj: Any, prefijo: str = ""):
    """Genera (json_path, valor_escalar) de un objeto JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_json(v, f"{prefijo}.{k}" if prefijo else k)
    elif isinstance(obj, list):
        for j, v in enumerate(obj):
            yield from _walk_json(v, f"{prefijo}[{j}]")
    elif obj is not None and not isinstance(obj, bool):
        yield prefijo, str(obj)


def _valores_de_cuerpo(cuerpo: str, tipo: str):
    """(clave, valor) de un cuerpo, según su content-type."""
    if not cuerpo:
        return
    tipo = (tipo or "").lower()
    if "json" in tipo:
        try:
            yield from _walk_json(json.loads(cuerpo))
            return
        except (json.JSONDecodeError, ValueError):
            pass
    if "form-urlencoded" in tipo:
        for k, v in parse_qsl(cuerpo, keep_blank_values=False):
            yield k, v
        return
    # Cuerpo desconocido: no lo despedazamos, pero rescatamos tokens largos
    # sueltos (un id reflejado en un HTML, por ejemplo).
    for m in re.finditer(r"[A-Za-z0-9_\-./=+]{6,}", cuerpo[:20000]):
        yield "raw", m.group(0)


def _cookies(headers: List[List[str]]):
    for k, v in headers:
        if k.lower() in ("cookie", "set-cookie"):
            for par in v.split(";"):
                if "=" in par:
                    nombre, valor = par.split("=", 1)
                    yield nombre.strip(), valor.strip()


def extraer_ocurrencias(mensajes: List[dict]) -> List[Ocurrencia]:
    """Aplana la captura en ocurrencias. Es la frontera entre 'mensajes' (lo
    que produce la captura) y el resto del pipeline."""
    ocs: List[Ocurrencia] = []

    def agregar(m, lado, ubic, clave, valor, contexto):
        valor = str(valor)
        if MIN_VALOR <= len(valor) <= MAX_VALOR:
            ocs.append(Ocurrencia(m["id"], m["session"], float(m.get("ts", 0)),
                                  lado, ubic, str(clave)[:80], valor, contexto))

    for m in mensajes:
        partes = urlsplit(m.get("url", ""))
        for idx, seg in enumerate(s for s in partes.path.split("/") if s):
            agregar(m, "request", "url_path", f"seg{idx}", unquote(seg), "url")
        for k, v in parse_qsl(partes.query, keep_blank_values=False):
            agregar(m, "request", "url_query", k, v, "url")

        for k, v in m.get("req_headers", []):
            if k.lower() not in ("cookie",):
                agregar(m, "request", "header", k, v, "header")
        for k, v in _cookies(m.get("req_headers", [])):
            agregar(m, "request", "cookie", k, v, "header")
        for k, v in _valores_de_cuerpo(m.get("req_body", ""), m.get("req_content_type", "")):
            agregar(m, "request", "body", k, v, m.get("req_content_type", "") or "body")

        for k, v in m.get("resp_headers", []):
            if k.lower() not in ("set-cookie",):
                agregar(m, "response", "header", k, v, "header")
        for k, v in _cookies(m.get("resp_headers", [])):
            agregar(m, "response", "cookie", k, v, "header")
        for k, v in _valores_de_cuerpo(m.get("resp_body", ""), m.get("resp_content_type", "")):
            agregar(m, "response", "body", k, v, m.get("resp_content_type", "") or "body")

    return ocs


# --- 1. Expansor de derivadas + hashes --------------------------------------
# Regla de oro: DECODIFICAR es finito (se hace cuando el valor parece encodeado),
# HASHEAR es forward y finito (siempre, contra un set fijo de algoritmos). Nunca
# se ENCODEA hacia adelante — eso explota. La asimetría es lo que lo hace
# tratable: para cazar id=8842 -> sha256(8842), hasheo 8842 y comparo contra el
# raw del otro lado.

_HASHES = ("md5", "sha1", "sha256")
_B64 = re.compile(r"^[A-Za-z0-9_\-+/]{8,}={0,2}$")


def _parece_base64(v: str) -> bool:
    return bool(_B64.match(v)) and len(v) % 4 in (0, 2, 3)


def _decodificar_base64(v: str) -> Optional[str]:
    try:
        crudo = base64.b64decode(v + "=" * (-len(v) % 4), validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        texto = crudo.decode("utf-8")
    except UnicodeDecodeError:
        return None
    imprimibles = sum(1 for c in texto if c.isprintable() or c in "\n\t")
    if texto and imprimibles / len(texto) > 0.9 and texto != v:
        return texto
    return None


def expandir_derivadas(valor: str, profundidad: int = 2) -> Dict[str, str]:
    """Todas las formas normalizadas bajo las que este valor podría matchear
    otra ocurrencia, cada una con la transform que la produjo.

    Dos ocurrencias se relacionan si sus sets de derivadas se intersecan; la
    transform del enlace sale de la clave que matcheó."""
    derivadas: Dict[str, str] = {valor: "identity"}

    def sumar(clave: str, transform: str):
        # La primera transform que llega a una clave gana (la más directa).
        if clave and clave not in derivadas and len(clave) >= MIN_VALOR:
            derivadas[clave] = transform

    ud = unquote(valor)
    if ud != valor:
        sumar(ud, "url_decode")

    if profundidad > 0 and _parece_base64(valor):
        dec = _decodificar_base64(valor)
        if dec:
            sumar(dec, "base64")
            try:
                obj = json.loads(dec)
                for jp, v in _walk_json(obj):
                    sumar(v, f"base64.json.{jp}")
            except (json.JSONDecodeError, ValueError):
                pass

    # Run numérico: /user/8842 ya viene como "8842", pero un "id-8842-x" no.
    digitos = re.findall(r"\d{3,}", valor)
    if len(digitos) == 1 and digitos[0] != valor:
        sumar(digitos[0], "numeric")

    # Hashes forward de la forma cruda y de la url-decodificada.
    for base in {valor, ud}:
        b = base.encode("utf-8", "replace")
        for algo in _HASHES:
            sumar(hashlib.new(algo, b).hexdigest(), algo)

    return derivadas


# --- 2. Cruce de contexto ---------------------------------------------------
# Un valor que entra como param de URL y sale en un text/html cruzó al parser
# de HTML: es donde vive el XSS/inyección. El que entra JSON y sale JSON, no.

def _familia_contexto(contexto: str) -> str:
    c = (contexto or "").lower()
    if "html" in c:
        return "html"
    if "json" in c:
        return "json"
    if c in ("url", "header"):
        return c
    if "xml" in c:
        return "xml"
    if "javascript" in c or "script" in c:
        return "js"
    return "otro"


# Cuánto "cuesta" cruzar de una familia a otra (0 = mismo parser, 1 = salto peligroso).
_PELIGRO_CRUCE = {
    ("url", "html"): 1.0, ("url", "js"): 1.0, ("url", "xml"): 0.8,
    ("json", "html"): 1.0, ("header", "html"): 1.0, ("json", "js"): 1.0,
    ("url", "json"): 0.4, ("json", "url"): 0.3, ("header", "json"): 0.4,
}


def score_cruce_contexto(origen: Ocurrencia, destino: Ocurrencia) -> Signal:
    fo, fd = _familia_contexto(origen.contexto), _familia_contexto(destino.contexto)
    if fo == fd:
        return Signal(0.0, f"mismo contexto ({fo})", {"desde": fo, "hasta": fd})
    peligro = _PELIGRO_CRUCE.get((fo, fd), 0.2)
    # Solo cuenta si el sink es un intérprete y el source es controlable.
    controlable = origen.lado == "request"
    score = peligro if controlable else peligro * 0.4
    return Signal(score, f"cruza {fo} -> {fd}" + ("" if controlable else " (sink->sink)"),
                  {"desde": fo, "hasta": fd, "controlable": controlable})


# --- 3. Clasificador de material de sesión ----------------------------------
# El token aparece en todas las requests -> correlaciona con todo. No se
# EXCLUYE (un token que cruza a text/html es session-fixation, señal real): se
# le baja el peso, salvo que cruce un límite de confianza. El discriminador no
# es el nombre del header, es la firma: alta frecuencia + constante/rotado.

@dataclass
class StatsSesion:
    total_requests: Dict[str, int] = field(default_factory=dict)   # session -> N
    freq_valor: Dict[Tuple[str, str], int] = field(default_factory=dict)  # (session,valor)->N msgs


def calcular_stats(ocs: List[Ocurrencia]) -> StatsSesion:
    st = StatsSesion()
    reqs_por_sesion: Dict[str, set] = {}
    valor_en_msgs: Dict[Tuple[str, str], set] = {}
    for o in ocs:
        reqs_por_sesion.setdefault(o.session, set()).add(o.mensaje_id)
        valor_en_msgs.setdefault((o.session, o.valor), set()).add(o.mensaje_id)
    st.total_requests = {s: len(v) for s, v in reqs_por_sesion.items()}
    st.freq_valor = {k: len(v) for k, v in valor_en_msgs.items()}
    return st


UMBRAL_CARRIED = 0.6      # aparece en >60% de las requests de la sesión


def score_material_sesion(oc: Ocurrencia, stats: StatsSesion,
                          hint_auth: Optional[set] = None) -> Signal:
    total = stats.total_requests.get(oc.session, 1)
    apariciones = stats.freq_valor.get((oc.session, oc.valor), 1)
    fraccion = apariciones / max(total, 1)
    es_hint = bool(hint_auth and oc.valor in hint_auth)

    if es_hint or (total >= 3 and fraccion >= UMBRAL_CARRIED):
        # Penalización, NO exclusión. El combinador la resta salvo cruce de contexto.
        motivo = "material de auth (hint)" if es_hint else \
            f"carried-credential (en {fraccion:.0%} de las requests)"
        return Signal(0.0, motivo, {"rol": "carried_credential", "penalidad": 0.7,
                                    "fraccion": fraccion})
    return Signal(1.0, "dato de negocio", {"rol": "business", "penalidad": 0.0,
                                           "fraccion": fraccion})


# --- 4. Entropía / unicidad -------------------------------------------------
# Baja el peso de las colisiones triviales (id=2, "true", page=1). Un match
# sobre un valor de 3 bits no significa nada; sobre uno de 60 bits, es señal.

def score_entropia(valor: str) -> Signal:
    if not valor:
        return Signal(0.0, "vacío")
    frecs: Dict[str, int] = {}
    for c in valor:
        frecs[c] = frecs.get(c, 0) + 1
    h_por_char = -sum((n / len(valor)) * math.log2(n / len(valor)) for n in frecs.values())
    bits = h_por_char * len(valor)
    # <12 bits ("2","true") -> ~0 ; >56 bits (token) -> 1
    score = max(0.0, min(1.0, (bits - 12) / 44))
    return Signal(score, f"{bits:.0f} bits", {"bits": bits})


# --- 5. Ownership sets (IDOR por dueño) -------------------------------------
# El IDOR entre tenants devuelve 200 en ambos roles; el status no cambia, el
# body sí. Pero difear bodies crudos marca todo (dos usuarios TIENEN data
# distinta). La señal precisa: la respuesta de A contiene un id cuyo dueño
# EXCLUSIVO es B. Dueño exclusivo = el id aparece en respuestas autenticadas de
# una sola sesión.

_ID_RE = re.compile(
    r"\b\d{3,}\b"                                             # numéricos
    r"|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"       # uuid
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def construir_ownership(mensajes: List[dict]) -> Dict[str, str]:
    """id -> sesión dueña, definida como la PRIMERA en el tiempo que lo devuelve
    en una respuesta autenticada (2xx con cuerpo).

    No se usa exclusividad global a propósito: el propio IDOR haría que el id
    aparezca en dos sesiones y se auto-descalificaría. El primero en verlo es el
    dueño legítimo; una aparición POSTERIOR en otra sesión es la violación.

    Límite conocido (a calibrar con tráfico real): un id público —un product_id
    que A ve primero y B ve legítimamente— daría falso positivo. Se corrige con
    una lista de ids compartidos o mirando si el id es de identidad (aparece en
    el /profile de la sesión). Por ahora queda como candidato a revisar."""
    orden = sorted(mensajes, key=lambda m: (m.get("ts", 0), m.get("id", 0)))
    dueño: Dict[str, str] = {}
    for m in orden:
        if not (200 <= m.get("status", 0) < 300):
            continue
        for mm in _ID_RE.finditer((m.get("resp_body", "") or "")[:40000]):
            dueño.setdefault(mm.group(0), m["session"])   # el primero gana
    return dueño


def candidatos_ownership(mensajes: List[dict],
                         ownership: Dict[str, str]) -> List[Candidato]:
    """Respuestas que devuelven ids de OTRA sesión. Es la señal de IDOR más
    limpia: viene con la evidencia armada."""
    cands: List[Candidato] = []
    for m in mensajes:
        if not (200 <= m.get("status", 0) < 300):
            continue
        sesion = m["session"]
        ajenos = []
        for mm in _ID_RE.finditer((m.get("resp_body", "") or "")[:40000]):
            dueño = ownership.get(mm.group(0))
            if dueño and dueño != sesion:
                ajenos.append((mm.group(0), dueño))
        if not ajenos:
            continue
        destino = Ocurrencia(m["id"], sesion, float(m.get("ts", 0)), "response",
                             "body", "id_ajeno", ajenos[0][0],
                             m.get("resp_content_type", "") or "body")
        c = Candidato("ownership", None, destino, "ownership_violation")
        c.senales["ownership"] = Signal(
            1.0, f"la respuesta de {sesion} devolvió {len(ajenos)} id(s) de otra sesión",
            {"ids_ajenos": ajenos[:10], "url": m.get("url", "")})
        cands.append(c)
    return cands


# --- Matcher: value-flows con ventana causal --------------------------------

VENTANA_REQS = 40          # N requests hacia adelante
VENTANA_SEG = 900          # o 15 minutos, lo que se cumpla primero


def emparejar(ocs: List[Ocurrencia]) -> List[Candidato]:
    """Enlaza source -> sink por derivadas, dentro de la ventana causal y de la
    misma sesión. Fuera de la ventana, un id=1 'reaparece' en todos lados."""
    # Índice por VALOR CRUDO (identity) solamente. El matching es asimétrico:
    # una derivada del source (identity, sha256, base64.json...) matchea el raw
    # del sink. Nunca derivada contra derivada — eso hace colisionar los hashes
    # de ambos lados y multiplica un hallazgo en 3 falsos ("md5/sha1/sha256").
    ordenadas = sorted(ocs, key=lambda o: (o.ts, o.mensaje_id))
    indice_raw: Dict[str, List[Ocurrencia]] = {}
    for o in ordenadas:
        indice_raw.setdefault(o.valor, []).append(o)

    cands: List[Candidato] = []
    # Un mismo (source, sink) puede matchear por varias derivadas; nos quedamos
    # con la más específica (identity/hash antes que numeric).
    mejor: Dict[Tuple[int, int], Candidato] = {}
    for origen in ordenadas:
        for clave, transform in expandir_derivadas(origen.valor).items():
            for destino in indice_raw.get(clave, []):
                if destino is origen or destino.session != origen.session:
                    continue
                if destino.ts < origen.ts or (destino.ts - origen.ts) > VENTANA_SEG:
                    continue
                if destino.mensaje_id == origen.mensaje_id and destino.lado == origen.lado:
                    continue
                key = (id(origen), id(destino))
                previo = mejor.get(key)
                if previo is None or _valor_transform(transform) > _valor_transform(previo.transform):
                    mejor[key] = Candidato("value_flow", origen, destino, transform)
    return list(mejor.values())


# --- Combinador: score de interés -------------------------------------------
# El ranking es DETERMINISTA. El LLM recibe el top-N ya filtrado; no rankea.
# Pesos separados por scorer para poder medir cuál mete ruido y ajustarlo solo.

PESOS = {
    "ownership": 1.0,
    "cruce_contexto": 0.9,
    "derivacion": 0.6,
    "entropia": 0.5,
}

# Cuánto vale cada transform como señal (identity y hash específico = alto;
# substring/numeric = bajo, matchean por casualidad).
VALOR_TRANSFORM = {
    "identity": 0.7, "url_decode": 0.7, "base64": 0.9,
    "md5": 0.95, "sha1": 0.95, "sha256": 0.95, "numeric": 0.3,
}


def _valor_transform(transform: str) -> float:
    if transform.startswith("base64.json"):
        return 0.9
    return VALOR_TRANSFORM.get(transform, 0.5)


def puntuar(candidatos: List[Candidato], stats: StatsSesion,
            hint_auth: Optional[set] = None) -> List[Candidato]:
    """Asigna interes a cada candidato y los ordena. Cada Signal queda guardada
    en el candidato para poder auditar por qué subió o bajó."""
    for c in candidatos:
        if c.tipo == "ownership":
            c.interes = PESOS["ownership"] * c.senales["ownership"].score
            continue

        o, d = c.origen, c.destino
        s_deriv = Signal(_valor_transform(c.transform), f"transform {c.transform}")
        s_ctx = score_cruce_contexto(o, d)
        s_ent = score_entropia(d.valor)
        s_mat = score_material_sesion(o, stats, hint_auth)

        c.senales = {"derivacion": s_deriv, "cruce_contexto": s_ctx,
                     "entropia": s_ent, "material_sesion": s_mat}

        base = (PESOS["derivacion"] * s_deriv.score
                + PESOS["cruce_contexto"] * s_ctx.score
                + PESOS["entropia"] * s_ent.score)
        # Material de sesión penaliza, SALVO que cruce un límite de confianza:
        # ahí el token reflejado en HTML es la señal, no el ruido.
        penalidad = s_mat.meta.get("penalidad", 0.0)
        if s_ctx.score >= 0.7:
            penalidad = 0.0
        c.interes = base * (1.0 - penalidad)

    return sorted(candidatos, key=lambda c: c.interes, reverse=True)


def analizar(mensajes: List[dict], top: int = 30) -> Dict[str, Any]:
    """Pipeline completo: valida, extrae, empareja, puntúa. Devuelve el top-N y
    un resumen del corpus (el nivel-1 de la divulgación progresiva)."""
    problemas = validar_mensajes(mensajes)
    if problemas:
        return {"error": "captura mal formada", "problemas": problemas[:20]}

    ocs = extraer_ocurrencias(mensajes)
    stats = calcular_stats(ocs)
    hint = {v for m in mensajes for v in m.get("auth_material", [])}

    ownership = construir_ownership(mensajes)
    candidatos = emparejar(ocs) + candidatos_ownership(mensajes, ownership)
    rankeados = puntuar(candidatos, stats, hint)

    sesiones = sorted({m["session"] for m in mensajes})
    return {
        "resumen": {
            "mensajes": len(mensajes), "ocurrencias": len(ocs),
            "sesiones": sesiones, "candidatos": len(candidatos),
            "ownership_violations": sum(1 for c in candidatos if c.tipo == "ownership"),
            "ids_con_dueño_exclusivo": len(ownership),
        },
        "top": rankeados[:top],
    }
