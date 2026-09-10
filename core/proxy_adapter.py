"""
Adaptador: tráfico capturado por el proxy (SQLite) -> mensajes del matcher.

Es la frontera entre lo que produce el proxy (core/proxy.py, tabla `flujos`) y
lo que consume el analizador (core/matcher.py, lista de `mensaje`). Todo lo de
correlación, taint y ownership razona sobre esos mensajes, así que acá es donde
se decide el campo más cargado: el FINGERPRINT DE SESIÓN.

Sobre el fingerprint: identifica una sesión de LOGIN, no a un usuario — sin
decodificar la cookie no hay forma de saber la identidad real. Por eso el
adaptador devuelve el hash crudo y, aparte, el material de auth en claro, para
que la UI o vos puedan mapear cada fingerprint a un rol ('cuenta_A', 'admin').
Dos capturas del mismo usuario en logins distintos dan fingerprints distintos:
es correcto (son sesiones distintas), y por eso el etiquetado humano importa.

Las peticiones sin material de auth caen en la sesión 'anonimo' — lo que separa
el tráfico pre-login del post-login, que ya es una distinción útil (qué es
alcanzable sin autenticarse).
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Nombres de cookie que suelen portar la sesión. Es un default razonable; la UI
# puede pasar la lista exacta cuando la conoce (acá sabemos que Yii usa
# '_identity-frontend' y 'advanced-frontend').
PATRON_COOKIE_AUTH = re.compile(
    r"identity|session|sess|_sid\b|^sid$|auth|token|advanced-frontend|phpsessid|"
    r"jsessionid|laravel_session|connect\.sid", re.I)

# Cookies que NO son auth aunque matcheen algo: analytics, consentimiento, CSRF
# (rota por request, ensuciaría el fingerprint).
PATRON_COOKIE_RUIDO = re.compile(
    r"^_ga|^_gid|^_gat|^_fbp|^_hj|utm_|consent|csrf|xsrf|__cf|cf_", re.I)


def _headers(j) -> List[List[str]]:
    """Lista de pares [clave, valor]. Filtra entradas malformadas en la raíz —
    algunos flujos reales traen headers que no son pares [k,v]— para que ningún
    consumidor (fingerprint, cookies, content-type) tenga que crashear."""
    # Punto UNICO donde se parsean headers guardados, asi que es donde va el
    # descifrado: marcas.py, el matcher y el fingerprint de sesion pasan todos
    # por aca. Lo que quedo en claro de una captura vieja vuelve igual.
    if isinstance(j, str):
        from core import secretos
        j = secretos.descifrar(j)
    try:
        crudo = json.loads(j) if isinstance(j, (str, bytes)) else (j or [])
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return [list(par) for par in crudo
            if isinstance(par, (list, tuple)) and len(par) == 2]


def _cookies_de(req_headers: List[List[str]]) -> List[Tuple[str, str]]:
    pares = []
    for par in req_headers:
        # Algunos flujos reales traen headers malformados (una entrada que no es
        # un par [clave, valor]); saltarlos en vez de crashear.
        if not (isinstance(par, (list, tuple)) and len(par) == 2):
            continue
        k, v = par
        if k.lower() == "cookie":
            for par in v.split(";"):
                if "=" in par:
                    nombre, valor = par.split("=", 1)
                    pares.append((nombre.strip(), valor.strip()))
    return pares


def material_auth(req_headers: List[List[str]],
                  nombres_cookie: Optional[List[str]] = None) -> List[str]:
    """Valores que constituyen la credencial de esta request: cookies de sesión
    + el header Authorization. Es lo que se hashea para el fingerprint y lo que
    el clasificador de material-de-sesión del matcher va a querer excluir del
    value-flow."""
    material: List[str] = []
    explicitos = {n.lower() for n in (nombres_cookie or [])}

    for nombre, valor in _cookies_de(req_headers):
        if PATRON_COOKIE_RUIDO.search(nombre):
            continue
        es_auth = (nombre.lower() in explicitos if explicitos
                   else bool(PATRON_COOKIE_AUTH.search(nombre)))
        if es_auth and len(valor) >= 8:
            material.append(f"{nombre}={valor}")

    for k, v in req_headers:
        if k.lower() == "authorization" and v.strip():
            material.append(v.strip())

    return sorted(set(material))


# Cookies de SESIÓN de servidor, por prioridad. Definen la identidad del
# fingerprint. Deliberadamente NO incluye remember-me/identity (van y vienen por
# ruta y fragmentarían la sesión) — esas quedan en material para redacción.
COOKIES_SESION = ("phpsessid", "jsessionid", "asp.net_sessionid", "sessionid",
                  "session_id", "laravel_session", "connect.sid", "sid",
                  "advanced-frontend")


# Un id de sesión normal tiene decenas de chars. Más que esto es un blob
# cifrado/serializado (la app guarda estado en la cookie): su valor CAMBIA
# dentro del mismo login, así que no sirve como identidad. En ese caso el
# fingerprint colapsa por NOMBRE de cookie ('autenticado'), no por valor, y el
# etiquetado humano separa cuentas si hiciera falta.
LARGO_BLOB = 80


def _id_sesion_primario(req_headers, nombres_cookie=None) -> Optional[str]:
    cookies = {n.lower(): v for n, v in _cookies_de(req_headers) if len(v) >= 8}
    def clave(nombre, valor):
        return f"{nombre}=blob" if len(valor) >= LARGO_BLOB else f"{nombre}={valor}"
    for pref in (nombres_cookie or []):
        if pref.lower() in cookies:
            return clave(pref.lower(), cookies[pref.lower()])
    for pref in COOKIES_SESION:
        if pref in cookies:
            return clave(pref, cookies[pref])
    for k, v in req_headers:                       # sin cookie de sesión: bearer
        if k.lower() == "authorization" and v.strip():
            return v.strip() if len(v) < LARGO_BLOB else "authorization=blob"
    return None


def huella_sesion(req_headers: List[List[str]],
                  nombres_cookie: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    """(fingerprint, material_auth). El fingerprint sale de UNA cookie de sesión
    estable, no del conjunto: incluir todas fragmentaba una misma sesión cuando
    una cookie secundaria aparecía solo en ciertas rutas. `material_auth` sí
    devuelve todo, para redactarlo y mostrarlo."""
    primario = _id_sesion_primario(req_headers, nombres_cookie)
    material = material_auth(req_headers, nombres_cookie)
    if not primario:
        return "anonimo", material
    h = hashlib.sha256(primario.encode("utf-8")).hexdigest()[:12]
    return f"s_{h}", material


# --- Cuerpos ----------------------------------------------------------------

def _cuerpo_texto(blob) -> str:
    """Decodifica un cuerpo a texto; binario -> '' (el matcher no lo indexa)."""
    if not blob:
        return ""
    if isinstance(blob, str):
        return blob
    try:
        texto = blob.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return ""
    control = sum(1 for c in texto[:2000] if ord(c) < 9)
    return texto if control < 5 else ""


def _content_type(headers: List[List[str]]) -> str:
    for k, v in headers:
        if k.lower() == "content-type":
            return v.split(";")[0].strip()
    return ""


# --- Conversión ------------------------------------------------------------

def _valores_sesion(headers: List[List[str]], nombres_cookie=None) -> List[str]:
    """Valores (no nombres) de las cookies de sesión de una request."""
    explicitos = {n.lower() for n in (nombres_cookie or [])}
    vals = []
    for nombre, valor in _cookies_de(headers):
        if PATRON_COOKIE_RUIDO.search(nombre):
            continue
        es_auth = (nombre.lower() in explicitos if explicitos
                   else bool(PATRON_COOKIE_AUTH.search(nombre)))
        if es_auth and len(valor) >= 8:
            vals.append(valor)
    return vals


def _set_cookie_sesion(headers: List[List[str]], nombres_cookie=None) -> List[str]:
    explicitos = {n.lower() for n in (nombres_cookie or [])}
    vals = []
    for k, v in headers:
        if k.lower() != "set-cookie":
            continue
        if "=" not in v:
            continue
        nombre, resto = v.split("=", 1)
        valor = resto.split(";")[0].strip()
        if PATRON_COOKIE_RUIDO.search(nombre.strip()):
            continue
        es_auth = (nombre.strip().lower() in explicitos if explicitos
                   else bool(PATRON_COOKIE_AUTH.search(nombre)))
        if es_auth and len(valor) >= 8:
            vals.append(valor)
    return vals


def sugerir_etiquetas(db_path: Path, nombres_cookie=None) -> Dict[str, str]:
    """Propone {fingerprint -> etiqueta} encadenando las rotaciones de sesión.

    Un PHPSESSID que rota (Set-Cookie de un valor nuevo en respuesta a una
    request que traía el viejo) es la MISMA sesión: se unen sus valores con
    union-find. Cada componente conexo = una sesión de login continua, aunque
    el id haya cambiado 5 veces. Los fragmentos de un mismo login colapsan en
    una sola etiqueta; un logout + login-como-otro rompe la cadena y da otra.

    Es una SUGERENCIA: vos la editás (poner 'cuenta_admin', fusionar dos), y esa
    etiqueta es la que el matcher usa como rol."""
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
    filas = con.execute("SELECT id, ts, req_headers, resp_headers FROM flujos "
                        "WHERE origen='captura' ORDER BY ts, id").fetchall()

    padre: Dict[str, str] = {}
    def find(x):
        padre.setdefault(x, x)
        while padre[x] != x:
            padre[x] = padre[padre[x]]; x = padre[x]
        return x
    def union(a, b):
        padre[find(a)] = find(b)

    fp_valores: Dict[str, set] = {}
    primer_ts: Dict[str, float] = {}
    for f in filas:
        req_h, resp_h = _headers(f["req_headers"]), _headers(f["resp_headers"])
        v_req = _valores_sesion(req_h, nombres_cookie)
        v_resp = _set_cookie_sesion(resp_h, nombres_cookie)
        for v in v_req[1:]:
            union(v_req[0], v)                      # cookies de una misma request
        for vn in v_resp:                           # rotación: nuevo valor = misma sesión
            find(vn)
            if v_req:
                union(vn, v_req[0])
        fp, _ = huella_sesion(req_h, nombres_cookie)
        if fp == "anonimo":
            continue
        fp_valores.setdefault(fp, set()).update(v_req)
        primer_ts.setdefault(fp, f["ts"])

    # componente (raíz) de cada fingerprint, y etiqueta ordenada por aparición
    comp_de_fp = {fp: find(next(iter(vals))) if vals else fp
                  for fp, vals in fp_valores.items()}
    comps = sorted(set(comp_de_fp.values()),
                   key=lambda c: min(primer_ts[fp] for fp in comp_de_fp if comp_de_fp[fp]==c))
    etiqueta_comp = {c: f"sesion_{i+1}" for i, c in enumerate(comps)}
    return {fp: etiqueta_comp[comp_de_fp[fp]] for fp in fp_valores}


def flujos_a_mensajes(db_path: Path,
                      nombres_cookie: Optional[List[str]] = None,
                      solo_hosts: Optional[List[str]] = None,
                      etiquetas: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Lee la base del proxy y devuelve la lista de `mensaje` del matcher.

    Solo toma tráfico capturado (no los reenvíos). `solo_hosts` acota a los
    dominios objetivo — clave, porque una captura real trae 90% de ruido
    (telemetría, analytics) que no querés analizar."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    filas = con.execute(
        "SELECT * FROM flujos WHERE origen = 'captura' ORDER BY id").fetchall()

    hosts = [h.lower() for h in (solo_hosts or [])]
    mensajes: List[Dict[str, Any]] = []
    for f in filas:
        if hosts and f["host"].lower() not in hosts and not any(
                f["host"].lower().endswith("." + h) for h in hosts):
            continue
        req_h = _headers(f["req_headers"])
        resp_h = _headers(f["resp_headers"])
        session, material = huella_sesion(req_h, nombres_cookie)
        # Si hay etiquetas (del humano o auto-sugeridas), el rol es la etiqueta,
        # no el fingerprint crudo — así las rotaciones colapsan en una sesión.
        if etiquetas and session in etiquetas:
            session = etiquetas[session]
        q = f"?{f['query']}" if f["query"] else ""
        mensajes.append({
            "id": f["id"], "session": session, "ts": f["ts"],
            "method": f["metodo"], "status": f["estado"],
            "url": f"{f['esquema']}://{f['host']}:{f['puerto']}{f['ruta']}{q}",
            "req_content_type": _content_type(req_h),
            "resp_content_type": f["resp_tipo"] or "",
            "req_headers": req_h, "resp_headers": resp_h,
            "req_body": _cuerpo_texto(f["req_body"]),
            "resp_body": _cuerpo_texto(f["resp_body"]),
            "auth_material": material,
        })
    return mensajes


def resumen_sesiones(mensajes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Por cada fingerprint: cuántas requests, qué hosts, y una MUESTRA del
    material de auth (para que el humano lo mapee a un rol). El material va
    enmascarado: nunca se expone el valor entero de una cookie de sesión."""
    from collections import defaultdict
    datos = defaultdict(lambda: {"requests": 0, "hosts": set(), "material": set()})
    for m in mensajes:
        d = datos[m["session"]]
        d["requests"] += 1
        d["hosts"].add(m["url"].split("/")[2])
        for x in m["auth_material"][:3]:
            nombre = x.split("=")[0]
            d["material"].add(nombre)
    salida = []
    for sesion, d in sorted(datos.items(), key=lambda kv: -kv[1]["requests"]):
        salida.append({
            "session": sesion, "requests": d["requests"],
            "hosts": sorted(d["hosts"]),
            "cookies_auth": sorted(d["material"]),
        })
    return salida
