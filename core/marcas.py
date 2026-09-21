"""
Marks on captured flows + retrieval of redacted context for the LLM.

A PARALLEL path to the matcher: the matcher classifies taint/IDOR automatically;
this is the opposite — the "interesting" classification is made by the human
(marks a flow with a label), and the machine does retrieval: given a flow id, it
assembles the context needed to reason about it.

Golden rule of redaction:
  · Session cookies, Authorization and CSRF are MASKED in ALL headers that enter
    the context (card, window, group). A raw PHPSESSID or _identity-frontend
    never leaves.
  · The BODY of the flow under analysis is NOT touched: it's exactly the payload
    the human wants to see (bugMessage=<script>...). The redaction is of headers,
    not of the content to analyze.

Reuses the helpers from core/proxy_adapter (fingerprint, cookie extraction); it
reimplements none of that.

(The persisted 'marcas' table and its columns, the returned dict keys and the
redaction marker's value are still Spanish on purpose: the table is an on-disk
contract for a later migration phase, and the marker text belongs to the i18n
phase.)
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

# This app's session cookies. They define what gets masked and the fingerprint.
SESSION_NAMES = ["PHPSESSID", "advanced-frontend", "_identity-frontend", "_csrf-frontend"]

REDACTED_MARK = "«redactado»"
# Cookies/headers to mask by name even if not in the explicit list.
_SENSITIVE_PATTERN = re.compile(r"session|sess|identity|auth|token|csrf|xsrf|sid\b|phpsessid", re.I)


# --- Table (idempotent) -----------------------------------------------------

def _connect(db_path) -> sqlite3.Connection:
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


# --- Mark / unmark / list ---------------------------------------------------

def mark_flow(db_path, flow_id: int, label: str, note: Optional[str] = None) -> int:
    con = _connect(db_path)
    cur = con.execute(
        "INSERT INTO marcas (flujo_id, etiqueta, nota, ts) VALUES (?,?,?,?)",
        (flow_id, label, note, time.time()))
    con.commit()
    return cur.lastrowid


def unmark_flow(db_path, mark_id: int) -> bool:
    con = _connect(db_path)
    cur = con.execute("DELETE FROM marcas WHERE id = ?", (mark_id,))
    con.commit()
    return cur.rowcount > 0


def list_marks(db_path) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute("""
        SELECT m.id AS marca_id, m.flujo_id, m.etiqueta, m.nota, m.ts,
               f.metodo, f.esquema, f.host, f.puerto, f.ruta, f.query, f.estado
        FROM marcas m LEFT JOIN flujos f ON f.id = m.flujo_id
        ORDER BY m.ts DESC
    """).fetchall()
    out = []
    for r in rows:
        out.append({
            "marca_id": r["marca_id"], "flujo_id": r["flujo_id"],
            "etiqueta": r["etiqueta"], "nota": r["nota"], "ts": r["ts"],
            "url": _url(r) if r["host"] else None, "estado": r["estado"],
        })
    return out


# --- Redaction --------------------------------------------------------------

# NAME=value where value stops at ';', ',' OR end of line. The ',' is key: this
# app folds cookies with ', ' and a split by ';' only hides the PHPSESSID inside
# the value of the neighboring cookie.
_COOKIE_PAIR = re.compile(r'([A-Za-z0-9_.\-]+)=([^;,]+)')


def _is_sensitive_cookie(name: str) -> bool:
    return name in SESSION_NAMES or bool(_SENSITIVE_PATTERN.search(name))


def _sensitive_values(req_h, resp_h) -> set:
    """Values to mask wherever they appear. Combines the adapter's helpers with a
    regex extraction (name=value), because the adapter splits cookies by ';' only
    and this capture uses ',' — without the regex, the PHPSESSID leaks."""
    vals = set(_valores_sesion(req_h, SESSION_NAMES))
    vals |= set(_set_cookie_sesion(resp_h, SESSION_NAMES))
    for k, v in list(req_h) + list(resp_h):
        kl = k.lower()
        if kl in ("cookie", "set-cookie"):
            for m in _COOKIE_PAIR.finditer(v):
                if _is_sensitive_cookie(m.group(1)):
                    vals.add(m.group(2).strip())
        elif kl in ("authorization", "proxy-authorization") or "csrf" in kl or "xsrf" in kl:
            if v.strip():
                vals.add(v.strip())
    return {v for v in vals if v and len(v) >= 6}


def _mask_values(text: str, sensitive: set) -> str:
    for v in sensitive:
        if v in text:
            text = text.replace(v, REDACTED_MARK)
    return text


def _redact_cookie(value: str) -> str:
    """Masks the VALUE of session/csrf cookies; keeps the names. Separator-
    agnostic (';' or ','): it cuts each value by regex."""
    def repl(m):
        return f"{m.group(1)}={REDACTED_MARK}" if _is_sensitive_cookie(m.group(1)) else m.group(0)
    return _COOKIE_PAIR.sub(repl, value)


def _redact_headers(headers: List[List[str]], sensitive: set) -> List[List[str]]:
    out = []
    for k, v in headers:
        kl = k.lower()
        if kl == "cookie":
            v = _redact_cookie(v)
        elif kl == "set-cookie":
            v = _redact_cookie(v)
        elif kl in ("authorization", "proxy-authorization") or "csrf" in kl or "xsrf" in kl:
            v = REDACTED_MARK
        else:
            v = _mask_values(v, sensitive)
        out.append([k, v])
    return out


# --- Context ----------------------------------------------------------------

def _url(row) -> str:
    q = f"?{row['query']}" if row["query"] else ""
    return f"{row['esquema']}://{row['host']}:{row['puerto']}{row['ruta']}{q}"


def _flow_row(con, fid) -> Optional[sqlite3.Row]:
    return con.execute("SELECT * FROM flujos WHERE id = ?", (fid,)).fetchone()


def flow_context(db_path, flow_id: int, window: int = 5,
                   redact: bool = True) -> Dict[str, Any]:
    """Everything needed to reason about a flow, with redacted headers and the
    marked flow's body intact."""
    con = _connect(db_path)
    f = _flow_row(con, flow_id)
    if f is None:
        return {"error": f"No existe el flujo {flow_id}"}

    req_h, resp_h = _headers(f["req_headers"]), _headers(f["resp_headers"])
    sensitive = _sensitive_values(req_h, resp_h)
    fingerprint, _ = huella_sesion(req_h, SESSION_NAMES)

    # With redact=False, the person explicitly asked for the real values
    # (session cookies, tokens): headers AS-IS. Warning: this makes the secrets
    # travel to the LLM provider. It's the user's decision.
    def _rh(headers):
        return _redact_headers(headers, sensitive) if redact else [list(x) for x in headers]

    # (a) flow card — redacted headers, BODY INTACT (it's the payload).
    ficha = {
        "id": f["id"], "method": f["metodo"], "url": _url(f), "status": f["estado"],
        "ts": f["ts"], "session": fingerprint,
        "req_content_type": _content_type(req_h),
        "resp_content_type": f["resp_tipo"] or "",
        "req_headers": _rh(req_h),
        "resp_headers": _rh(resp_h),
        # unredacted: this is what the human wants to analyze
        "req_body": _cuerpo_texto(f["req_body"]),
        "resp_body": _cuerpo_texto(f["resp_body"]),
    }

    # (b) temporal window of the SAME session fingerprint.
    # With an authenticated fingerprint, the window is that session. With
    # 'anonimo' (which mixes all the login-less traffic from every host) it's
    # narrowed to the same host, otherwise the lead-in is google/mozilla noise.
    capturas = con.execute(
        "SELECT * FROM flujos WHERE origen = 'captura' ORDER BY ts, id").fetchall()
    host_only = fingerprint == "anonimo"
    same = [r for r in capturas
            if huella_sesion(_headers(r["req_headers"]), SESSION_NAMES)[0] == fingerprint
            and (not host_only or r["host"] == f["host"])]
    idx = next((i for i, r in enumerate(same) if r["id"] == flow_id), None)
    window_rows = []
    if idx is not None:
        for r in same[max(0, idx - window): idx + window + 1]:
            rh = _headers(r["req_headers"])
            sr = _sensitive_values(rh, _headers(r["resp_headers"]))
            window_rows.append({
                "id": r["id"], "method": r["metodo"], "status": r["estado"],
                "url": _url(r) if not redact else _mask_values(_url(r), sr),
                "ts": r["ts"], "es_este": r["id"] == flow_id,
                "req_headers": _rh(rh) if not redact else _redact_headers(rh, sr),
            })

    # (c) group by endpoint: same host+path, is this status anomalous?
    grupo = con.execute(
        "SELECT id, estado, ts FROM flujos WHERE origen='captura' AND host=? AND ruta=? ORDER BY ts",
        (f["host"], f["ruta"])).fetchall()
    statuses = Counter(r["estado"] for r in grupo)
    this_status = f["estado"]
    anomalous = statuses[this_status] <= max(1, len(grupo) // 5) and len(grupo) > 1

    grupo_endpoint = {
        "host": f["host"], "ruta": f["ruta"], "total": len(grupo),
        "estados": dict(statuses), "este_status": this_status, "anomalo": anomalous,
        "otros": [{"id": r["id"], "status": r["estado"], "ts": r["ts"]}
                  for r in grupo if r["id"] != flow_id][:20],
    }

    # (d) marks already placed on this flow.
    marcas = [dict(r) for r in con.execute(
        "SELECT id AS marca_id, etiqueta, nota, ts FROM marcas WHERE flujo_id=? ORDER BY ts",
        (flow_id,)).fetchall()]

    return {"flujo": ficha, "ventana": window_rows,
            "grupo_endpoint": grupo_endpoint, "marcas": marcas,
            "redactado": redact}
