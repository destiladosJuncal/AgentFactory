"""
Marcas sobre flujos capturados + retrieval de contexto redactado para el LLM.

Camino PARALELO al matcher: el matcher clasifica taint/IDOR automáticamente;
esto es lo contrario — la clasificación de "interesante" la hace el humano
(marca un flujo con una etiqueta), y la máquina hace retrieval: dado un id de
flujo, arma el contexto necesario para razonar sobre él.

Regla de oro de la redacción:
  · Se ENMASCARAN cookies de sesión, Authorization y CSRF en TODOS los headers
    que entran al contexto (ficha, ventana, grupo). Nunca sale un PHPSESSID ni
    un _identity-frontend crudo.
  · NO se toca el BODY del flujo bajo análisis: es justo el payload que el
    humano quiere ver (bugMessage=<script>...). La redacción es de headers, no
    del contenido a analizar.

Reusa los helpers de core/proxy_adapter (fingerprint, extracción de cookies);
no reimplementa nada de eso.
"""

import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.proxy_adapter import (
    _content_type, _cookies_de, _cuerpo_texto, _headers, _set_cookie_sesion,
    _valores_sesion, huella_sesion,
)

# Cookies de sesión de esta app. Definen qué se enmascara y el fingerprint.
NOMBRES_SESION = ["PHPSESSID", "advanced-frontend", "_identity-frontend", "_csrf-frontend"]

MARCA = "«redactado»"
# Cookies/headers a enmascarar por nombre aunque no estén en la lista explícita.
_PATRON_SENSIBLE = re.compile(r"session|sess|identity|auth|token|csrf|xsrf|sid\b|phpsessid", re.I)


# --- Tabla (idempotente) ----------------------------------------------------

def _con(db_path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS marcas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flujo_id INTEGER,
            etiqueta TEXT,
            nota TEXT,
            ts REAL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_marcas_flujo ON marcas(flujo_id)")
    con.commit()
    return con


# --- Marcar / desmarcar / listar --------------------------------------------

def marcar_flujo(db_path, flujo_id: int, etiqueta: str, nota: Optional[str] = None) -> int:
    con = _con(db_path)
    cur = con.execute(
        "INSERT INTO marcas (flujo_id, etiqueta, nota, ts) VALUES (?,?,?,?)",
        (flujo_id, etiqueta, nota, time.time()))
    con.commit()
    return cur.lastrowid


def desmarcar_flujo(db_path, marca_id: int) -> bool:
    con = _con(db_path)
    cur = con.execute("DELETE FROM marcas WHERE id = ?", (marca_id,))
    con.commit()
    return cur.rowcount > 0


def listar_marcas(db_path) -> List[Dict[str, Any]]:
    con = _con(db_path)
    filas = con.execute("""
        SELECT m.id AS marca_id, m.flujo_id, m.etiqueta, m.nota, m.ts,
               f.metodo, f.esquema, f.host, f.puerto, f.ruta, f.query, f.estado
        FROM marcas m LEFT JOIN flujos f ON f.id = m.flujo_id
        ORDER BY m.ts DESC
    """).fetchall()
    out = []
    for r in filas:
        out.append({
            "marca_id": r["marca_id"], "flujo_id": r["flujo_id"],
            "etiqueta": r["etiqueta"], "nota": r["nota"], "ts": r["ts"],
            "url": _url(r) if r["host"] else None, "estado": r["estado"],
        })
    return out


# --- Redacción --------------------------------------------------------------

# NAME=value donde value corta en ';', ',' O fin de línea. El ',' es clave:
# esta app pliega cookies con ', ' y un split solo por ';' esconde el PHPSESSID
# dentro del valor de la cookie de al lado.
_COOKIE_PAR = re.compile(r'([A-Za-z0-9_.\-]+)=([^;,]+)')


def _es_cookie_sensible(nombre: str) -> bool:
    return nombre in NOMBRES_SESION or bool(_PATRON_SENSIBLE.search(nombre))


def _valores_sensibles(req_h, resp_h) -> set:
    """Valores a enmascarar donde aparezcan. Combina los helpers del adapter con
    una extracción por regex (nombre=valor), porque el adapter parte cookies
    solo por ';' y esta captura usa ',' — sin el regex, se fuga el PHPSESSID."""
    vals = set(_valores_sesion(req_h, NOMBRES_SESION))
    vals |= set(_set_cookie_sesion(resp_h, NOMBRES_SESION))
    for k, v in list(req_h) + list(resp_h):
        kl = k.lower()
        if kl in ("cookie", "set-cookie"):
            for m in _COOKIE_PAR.finditer(v):
                if _es_cookie_sensible(m.group(1)):
                    vals.add(m.group(2).strip())
        elif kl in ("authorization", "proxy-authorization") or "csrf" in kl or "xsrf" in kl:
            if v.strip():
                vals.add(v.strip())
    return {v for v in vals if v and len(v) >= 6}


def _mask_valores(texto: str, sensibles: set) -> str:
    for v in sensibles:
        if v in texto:
            texto = texto.replace(v, MARCA)
    return texto


def _redactar_cookie(valor: str) -> str:
    """Enmascara el VALOR de las cookies de sesión/csrf; conserva los nombres.
    Agnóstico al separador (';' o ','): corta cada valor por regex."""
    def repl(m):
        return f"{m.group(1)}={MARCA}" if _es_cookie_sensible(m.group(1)) else m.group(0)
    return _COOKIE_PAR.sub(repl, valor)


def _redactar_headers(headers: List[List[str]], sensibles: set) -> List[List[str]]:
    out = []
    for k, v in headers:
        kl = k.lower()
        if kl == "cookie":
            v = _redactar_cookie(v)
        elif kl == "set-cookie":
            v = _redactar_cookie(v)
        elif kl in ("authorization", "proxy-authorization") or "csrf" in kl or "xsrf" in kl:
            v = MARCA
        else:
            v = _mask_valores(v, sensibles)
        out.append([k, v])
    return out


# --- Contexto ---------------------------------------------------------------

def _url(fila) -> str:
    q = f"?{fila['query']}" if fila["query"] else ""
    return f"{fila['esquema']}://{fila['host']}:{fila['puerto']}{fila['ruta']}{q}"


def _fila_flujo(con, fid) -> Optional[sqlite3.Row]:
    return con.execute("SELECT * FROM flujos WHERE id = ?", (fid,)).fetchone()


def contexto_flujo(db_path, flujo_id: int, ventana: int = 5,
                   redactar: bool = True) -> Dict[str, Any]:
    """Todo lo necesario para razonar sobre un flujo, con headers redactados y
    el body del flujo marcado intacto."""
    con = _con(db_path)
    f = _fila_flujo(con, flujo_id)
    if f is None:
        return {"error": f"No existe el flujo {flujo_id}"}

    req_h, resp_h = _headers(f["req_headers"]), _headers(f["resp_headers"])
    sensibles = _valores_sensibles(req_h, resp_h)
    fingerprint, _ = huella_sesion(req_h, NOMBRES_SESION)

    # Con redactar=False, la persona pidió explícitamente los valores reales
    # (cookies de sesión, tokens): headers TAL CUAL. Advertencia: esto hace que
    # los secretos viajen al proveedor del LLM. Es una decisión del usuario.
    def _rh(headers):
        return _redactar_headers(headers, sensibles) if redactar else [list(x) for x in headers]

    # (a) ficha del flujo — headers redactados, BODY INTACTO (es el payload).
    ficha = {
        "id": f["id"], "method": f["metodo"], "url": _url(f), "status": f["estado"],
        "ts": f["ts"], "session": fingerprint,
        "req_content_type": _content_type(req_h),
        "resp_content_type": f["resp_tipo"] or "",
        "req_headers": _rh(req_h),
        "resp_headers": _rh(resp_h),
        # sin redactar: esto es lo que el humano quiere analizar
        "req_body": _cuerpo_texto(f["req_body"]),
        "resp_body": _cuerpo_texto(f["resp_body"]),
    }

    # (b) ventana temporal del MISMO fingerprint de sesión.
    # Con fingerprint autenticado, la ventana es esa sesión. Con 'anonimo'
    # (que mezcla todo el tráfico sin login de todos los hosts) se acota al
    # mismo host, si no el lead-in es ruido de google/mozilla.
    capturas = con.execute(
        "SELECT * FROM flujos WHERE origen = 'captura' ORDER BY ts, id").fetchall()
    solo_host = fingerprint == "anonimo"
    misma = [r for r in capturas
             if huella_sesion(_headers(r["req_headers"]), NOMBRES_SESION)[0] == fingerprint
             and (not solo_host or r["host"] == f["host"])]
    idx = next((i for i, r in enumerate(misma) if r["id"] == flujo_id), None)
    vent = []
    if idx is not None:
        for r in misma[max(0, idx - ventana): idx + ventana + 1]:
            rh = _headers(r["req_headers"])
            sr = _valores_sensibles(rh, _headers(r["resp_headers"]))
            vent.append({
                "id": r["id"], "method": r["metodo"], "status": r["estado"],
                "url": _url(r) if not redactar else _mask_valores(_url(r), sr),
                "ts": r["ts"], "es_este": r["id"] == flujo_id,
                "req_headers": _rh(rh) if not redactar else _redactar_headers(rh, sr),
            })

    # (c) grupo por endpoint: mismos host+ruta, ¿este status es anómalo?
    grupo = con.execute(
        "SELECT id, estado, ts FROM flujos WHERE origen='captura' AND host=? AND ruta=? ORDER BY ts",
        (f["host"], f["ruta"])).fetchall()
    estados = Counter(r["estado"] for r in grupo)
    este = f["estado"]
    anomalo = estados[este] <= max(1, len(grupo) // 5) and len(grupo) > 1

    grupo_endpoint = {
        "host": f["host"], "ruta": f["ruta"], "total": len(grupo),
        "estados": dict(estados), "este_status": este, "anomalo": anomalo,
        "otros": [{"id": r["id"], "status": r["estado"], "ts": r["ts"]}
                  for r in grupo if r["id"] != flujo_id][:20],
    }

    # (d) marcas ya puestas sobre este flujo.
    marcas = [dict(r) for r in con.execute(
        "SELECT id AS marca_id, etiqueta, nota, ts FROM marcas WHERE flujo_id=? ORDER BY ts",
        (flujo_id,)).fetchall()]

    return {"flujo": ficha, "ventana": vent,
            "grupo_endpoint": grupo_endpoint, "marcas": marcas,
            "redactado": redactar}
