# -*- coding: utf-8 -*-
"""
Tests de core/proxy_tool.py sobre una captura sintética.

Lo que se verifica es lo que antes fallaba:

  1. Pedir un sitio no arrastra dominios parecidos ni lookalikes.
  2. Un nombre ambiguo devuelve candidatos en vez de elegir uno.
  3. Ninguna respuesta filtra la ruta de la base de datos.
  4. La lectura en bloque devuelve cuerpos y NINGÚN header.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import proxy_tool  # noqa: E402

COOKIE_SECRETA = "li_at=SUPERSECRETO123456789; lang=es"

FLUJOS = [
    # (host, ruta, tipo, cuerpo)
    ("www.linkedin.com", "/jobs/1", "application/json", '{"puesto":"Dev"}'),
    ("linkedin.com", "/jobs/2", "application/json", '{"puesto":"QA"}'),
    ("notlinkedin.com", "/jobs/3", "application/json", '{"puesto":"TRAMPA"}'),
    ("phish-linkedin.ru", "/jobs/4", "application/json", '{"puesto":"PHISH"}'),
    ("linkedin.evil.com", "/jobs/5", "application/json", '{"puesto":"EVIL"}'),
    ("google.com", "/search", "text/html", "<html>g</html>"),
    ("google.com.ar.phish.net", "/search", "text/html", "<html>LOOKALIKE</html>"),
    ("largo.example.com", "/big", "text/plain", "X" * 5000),
    ("acme.com", "/a", "application/json", "{}"),
    ("acme.io", "/b", "application/json", "{}"),
]


@pytest.fixture
def captura(tmp_path, monkeypatch):
    """Arma una sesion.db sintética y hace que proxy_tool la use."""
    db = tmp_path / "sesion.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE flujos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, origen TEXT, ref INTEGER,
            metodo TEXT, esquema TEXT, host TEXT, puerto INTEGER,
            ruta TEXT, query TEXT,
            req_headers TEXT, req_body BLOB, req_trunc INTEGER,
            estado INTEGER, resp_headers TEXT, resp_body BLOB, resp_trunc INTEGER,
            resp_tipo TEXT, ms REAL
        )""")
    ahora = time.time()
    for i, (host, ruta, tipo, cuerpo) in enumerate(FLUJOS):
        con.execute(
            "INSERT INTO flujos (ts, origen, metodo, esquema, host, puerto, ruta, "
            "query, req_headers, req_body, estado, resp_headers, resp_body, resp_tipo) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ahora + i, "captura", "GET", "https", host, 443, ruta, "",
             json.dumps([["Cookie", COOKIE_SECRETA]]), b"", 200,
             json.dumps([["Set-Cookie", COOKIE_SECRETA]]), cuerpo.encode(), tipo))
    con.commit()
    con.close()
    monkeypatch.setattr(proxy_tool, "_db", lambda: db)
    return db


# --- 1. No arrastra dominios ajenos ----------------------------------------

def test_buscar_no_trae_dominios_parecidos(captura):
    r = proxy_tool.buscar_en_captura(sitio="linkedin.com")
    hosts = {f["sitio"] for f in r["flujos"]}
    assert hosts == {"linkedin.com", "www.linkedin.com"}
    # Los tres que la comparación por subcadena metía:
    assert "notlinkedin.com" not in hosts
    assert "phish-linkedin.ru" not in hosts
    assert "linkedin.evil.com" not in hosts


def test_buscar_no_trae_el_lookalike_de_google(captura):
    r = proxy_tool.buscar_en_captura(sitio="google.com")
    hosts = {f["sitio"] for f in r["flujos"]}
    assert hosts == {"google.com"}
    assert "google.com.ar.phish.net" not in hosts


def test_el_contenido_de_la_trampa_nunca_aparece(captura):
    r = proxy_tool.extraer_de_captura(sitio="linkedin.com")
    todo = json.dumps(r, ensure_ascii=False)
    for trampa in ("TRAMPA", "PHISH", "EVIL"):
        assert trampa not in todo


# --- 2. Ambigüedad: candidatos, no adivinanza ------------------------------

def test_nombre_ambiguo_devuelve_candidatos(captura):
    r = proxy_tool.buscar_en_captura(sitio="acme")
    assert "flujos" not in r
    assert [c["sitio"] for c in r["candidatos"]] == ["acme.com", "acme.io"]
    assert "preguntale" in r["aviso"].lower()


def test_nombre_suelto_no_ambiguo_resuelve(captura):
    r = proxy_tool.buscar_en_captura(sitio="linkedin")
    assert r["sitio"] == "linkedin.com"
    assert {f["sitio"] for f in r["flujos"]} == {"linkedin.com", "www.linkedin.com"}


def test_sitio_inexistente_ofrece_la_lista(captura):
    r = proxy_tool.buscar_en_captura(sitio="instagram.com")
    assert r["candidatos"] == []
    assert "linkedin.com" in r["sitios"]


# --- 3. La ruta de la base no se filtra ------------------------------------

@pytest.mark.parametrize("llamada", [
    lambda: proxy_tool.buscar_en_captura(sitio="linkedin.com"),
    lambda: proxy_tool.extraer_de_captura(sitio="linkedin.com"),
    lambda: proxy_tool.listar_sitios_capturados(),
])
def test_ninguna_respuesta_expone_la_base(captura, llamada):
    texto = json.dumps(llamada(), ensure_ascii=False)
    assert "sesion.db" not in texto
    assert '"db"' not in texto


def test_el_schema_no_invita_a_leer_la_base():
    esquema = json.dumps(proxy_tool.TOOLS_SCHEMA_PROXY, ensure_ascii=False).lower()
    # La descripción vieja decía "leyendo la tabla flujos de esa db".
    assert "tabla `flujos`" not in esquema
    assert "sqlite" not in esquema


# --- 4. La lectura en bloque no devuelve headers ---------------------------

def test_extraer_devuelve_cuerpos(captura):
    r = proxy_tool.extraer_de_captura(sitio="linkedin.com")
    assert r["total"] == 2
    cuerpos = " ".join(f["resp_body"] for f in r["flujos"])
    assert "Dev" in cuerpos and "QA" in cuerpos


def test_extraer_no_filtra_la_cookie_de_sesion(captura):
    r = proxy_tool.extraer_de_captura(sitio="linkedin.com")
    texto = json.dumps(r, ensure_ascii=False)
    assert "SUPERSECRETO123456789" not in texto
    assert "li_at" not in texto
    assert "req_headers" not in texto
    assert "resp_headers" not in texto


def test_extraer_trunca_los_cuerpos_largos(captura):
    r = proxy_tool.extraer_de_captura(sitio="example.com", max_chars=500)
    cuerpo = r["flujos"][0]["resp_body"]
    assert cuerpo.endswith("… (truncado)")
    assert len(cuerpo) == 500 + len("… (truncado)")


def test_extraer_tiene_un_piso_de_200_caracteres(captura):
    # Pedir 5 caracteres no sirve para entender una estructura: hay un piso.
    r = proxy_tool.extraer_de_captura(sitio="example.com", max_chars=5)
    assert len(r["flujos"][0]["resp_body"]) == 200 + len("… (truncado)")


# --- listar_sitios_capturados ----------------------------------------------

def test_listar_agrupa_por_dominio(captura):
    r = proxy_tool.listar_sitios_capturados()
    sitios = {s["sitio"] for s in r["sitios"]}
    assert "linkedin.com" in sitios
    assert "phish.net" in sitios          # el lookalike se ve como lo que es
    entrada = next(s for s in r["sitios"] if s["sitio"] == "linkedin.com")
    assert sorted(entrada["hosts"]) == ["linkedin.com", "www.linkedin.com"]
    assert entrada["flujos"] == 2


# --- 5. contexto_flujo_proxy: headers no-secretos INTACTOS, secretos no ------
#
# El agente concluyó mal una vez que "todos los headers vienen redactados" y por
# eso no podía comparar user-agent/accept-language entre flujos. La redacción es
# quirúrgica: solo cookies de sesión / Authorization / CSRF. Esto lo fija.

@pytest.fixture
def captura_rica(tmp_path, monkeypatch):
    db = tmp_path / "sesion.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE flujos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, origen TEXT, ref INTEGER,
            metodo TEXT, esquema TEXT, host TEXT, puerto INTEGER,
            ruta TEXT, query TEXT,
            req_headers TEXT, req_body BLOB, req_trunc INTEGER,
            estado INTEGER, resp_headers TEXT, resp_body BLOB, resp_trunc INTEGER,
            resp_tipo TEXT, ms REAL
        )""")
    con.execute("""
        CREATE TABLE marcas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flujo_id INTEGER, etiqueta TEXT, nota TEXT, ts REAL
        )""")
    req_headers = [
        ["User-Agent", "Mozilla/5.0 Firefox/130"],
        ["Accept-Language", "es-AR,es;q=0.9"],
        ["Origin", "https://prometeoapi.com"],
        ["Referer", "https://prometeoapi.com/panel"],
        ["Authorization", "Bearer TOKENSECRETO999"],
        ["Cookie", "sessionid=SUPERSECRETO123456789; theme=dark"],
    ]
    con.execute(
        "INSERT INTO flujos (ts, origen, metodo, esquema, host, puerto, ruta, "
        "query, req_headers, req_body, estado, resp_headers, resp_body, resp_tipo) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), "captura", "GET", "https", "prometeoapi.com", 443, "/api/x",
         "", json.dumps(req_headers), b"", 200,
         json.dumps([["Content-Type", "application/json"]]), b"{}", "application/json"))
    con.commit()
    con.close()
    monkeypatch.setattr(proxy_tool, "_db", lambda: db)
    return db


def test_contexto_devuelve_headers_no_secretos_intactos(captura_rica):
    r = proxy_tool.contexto_de_flujo(1, ventana=0, redactar=True)
    assert "error" not in r
    hdrs = {k.lower(): v for k, v in r["flujo"]["req_headers"]}
    # Lo que ANTES el agente creía imposible de ver:
    assert hdrs["user-agent"] == "Mozilla/5.0 Firefox/130"
    assert hdrs["accept-language"] == "es-AR,es;q=0.9"
    assert hdrs["origin"] == "https://prometeoapi.com"
    assert hdrs["referer"] == "https://prometeoapi.com/panel"


def test_contexto_enmascara_solo_los_secretos(captura_rica):
    r = proxy_tool.contexto_de_flujo(1, ventana=0, redactar=True)
    hdrs = {k.lower(): v for k, v in r["flujo"]["req_headers"]}
    # Authorization y la cookie de sesión: enmascarados.
    assert "TOKENSECRETO999" not in hdrs["authorization"]
    assert "SUPERSECRETO123456789" not in hdrs["cookie"]
    # Pero la cookie no-secreta del mismo header sobrevive.
    assert "theme=dark" in hdrs["cookie"]
    # Y en ningún lado del contexto aparece el secreto en claro.
    texto = json.dumps(r, ensure_ascii=False)
    assert "TOKENSECRETO999" not in texto
    assert "SUPERSECRETO123456789" not in texto


def test_ver_cuerpo_flujo_apunta_a_contexto_para_headers(captura_rica):
    # La pista va en el PROPIO resultado, donde el modelo la lee al decidir.
    r = proxy_tool.ver_cuerpo_flujo(1)
    assert "req_headers" not in r and "resp_headers" not in r
    assert "contexto_flujo_proxy" in r["nota_headers"]


def test_el_schema_dice_donde_ver_los_headers():
    esquema = json.dumps(proxy_tool.TOOLS_SCHEMA_PROXY, ensure_ascii=False)
    ctx = next(t for t in proxy_tool.TOOLS_SCHEMA_PROXY
               if t["function"]["name"] == "contexto_flujo_proxy")
    desc = ctx["function"]["description"]
    # La tool de headers se anuncia como la que los trae, con redacción quirúrgica.
    assert "HEADERS" in desc and "user-agent" in desc.lower()
    # Las dos que no los traen remiten a ella.
    assert esquema.count("contexto_flujo_proxy") >= 3
