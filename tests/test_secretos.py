# -*- coding: utf-8 -*-
"""
Tests del cifrado en reposo de los headers.

Lo que tiene que quedar demostrado:

  1. Un SELECT crudo a la base devuelve bytes ilegibles, no la cookie.
  2. Los consumidores internos siguen viendo los valores reales (si no, se
     rompen el repetidor y el agrupado de sesiones).
  3. Una captura vieja, guardada en claro, se sigue leyendo.
  4. Un script que vaya a buscar la clave o la base dispara el diálogo, con
     "permitir siempre" por conversación.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ejecucion, secretos  # noqa: E402
from core import proxy_adapter  # noqa: E402

COOKIE = "li_at=SUPERSECRETO123456789; lang=es"
HEADERS = [["Host", "linkedin.com"], ["Cookie", COOKIE],
           ["Authorization", "Bearer TOKEN-ABCDEF"]]


@pytest.fixture
def datos(tmp_path, monkeypatch):
    """Carpeta de datos aislada, con su propia clave."""
    monkeypatch.setenv("AGENTE_DATOS", str(tmp_path))
    secretos.forget()
    yield tmp_path
    secretos.forget()


# --- 1. La base ya no entrega la cookie ------------------------------------

def test_cifrar_no_deja_el_secreto_visible(datos):
    cifrado = secretos.encrypt(json.dumps(HEADERS))
    assert secretos.is_encrypted(cifrado)
    assert "SUPERSECRETO123456789" not in cifrado
    assert "li_at" not in cifrado
    assert "Bearer" not in cifrado


def test_un_select_crudo_no_sirve(datos, tmp_path):
    """Simula lo que hace un script: abrir la base y leer la columna."""
    db = tmp_path / "sesion.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE flujos (id INTEGER PRIMARY KEY, req_headers TEXT)")
    con.execute("INSERT INTO flujos (req_headers) VALUES (?)",
                (secretos.encrypt(json.dumps(HEADERS)),))
    con.commit()
    leido = con.execute("SELECT req_headers FROM flujos").fetchone()[0]
    con.close()

    assert "SUPERSECRETO123456789" not in leido
    assert leido.startswith(secretos.PREFIX)


# --- 2. Lo interno sigue funcionando ---------------------------------------

def test_los_consumidores_internos_ven_el_valor_real(datos):
    # _headers() es el punto unico por el que pasan marcas, el matcher y el
    # fingerprint de sesion. Si aca no descifra, se rompe el agrupado.
    cifrado = secretos.encrypt(json.dumps(HEADERS))
    pares = proxy_adapter._headers(cifrado)
    assert ["Cookie", COOKIE] in pares
    assert ["Authorization", "Bearer TOKEN-ABCDEF"] in pares


def test_ida_y_vuelta(datos):
    for original in ("", "[]", json.dumps(HEADERS), "acentos: ñoño áéíóú"):
        assert secretos.decrypt(secretos.encrypt(original)) == original


def test_la_clave_queda_fuera_del_codigo(datos):
    secretos.encrypt("x")
    assert secretos.key_path().exists()
    assert secretos.key_path().parent == datos


# --- 3. Compatibilidad con capturas viejas ---------------------------------

def test_lo_guardado_en_claro_se_sigue_leyendo(datos):
    en_claro = json.dumps(HEADERS)
    assert not secretos.is_encrypted(en_claro)
    assert secretos.decrypt(en_claro) == en_claro
    assert proxy_adapter._headers(en_claro) == [list(p) for p in HEADERS]


def test_clave_corrupta_no_rompe_la_app(datos):
    secretos.encrypt("x")
    secretos.key_path().write_bytes(b"esto-no-es-una-clave")
    secretos.forget()
    # Se rehace la clave en vez de reventar.
    assert secretos.available()


def test_dato_ilegible_devuelve_vacio_en_vez_de_excepcion(datos):
    secretos.encrypt("x")
    basura = secretos.PREFIX + "bWFsbG8="
    assert secretos.decrypt(basura) == ""


# --- 4. El gate de lectura -------------------------------------------------

@pytest.mark.parametrize("codigo", [
    "open('clave-captura.key').read()",
    "from core import secretos",
    "from core.secretos import descifrar",
    "secretos.decrypt(fila)",
    "sqlite3.connect('/x/_proxy/sesion.db')",
    r"sqlite3.connect(r'C:\datos\_proxy\sesion.db')",
])
def test_tocar_las_credenciales_pide_permiso(codigo):
    hallazgos = ejecucion.analizar_riesgo(codigo, es_python=True)
    claves = [c for c, _ in hallazgos]
    assert "credenciales" in claves or "captura-cruda" in claves, codigo


@pytest.mark.parametrize("codigo", [
    "print('hola')",
    "import json; json.loads(texto)",
    "for f in flujos: print(f['resp_body'])",
])
def test_codigo_inocuo_no_molesta(codigo):
    claves = [c for c, _ in ejecucion.analizar_riesgo(codigo, es_python=True)]
    assert "credenciales" not in claves
    assert "captura-cruda" not in claves


def test_permitir_siempre_vale_para_la_conversacion(monkeypatch):
    pedidos = []

    def confirmador(resumen, detalle, clave):
        pedidos.append(clave)
        return "siempre"

    monkeypatch.setattr(ejecucion, "CONFIRMADOR", confirmador)
    permisos = set()
    codigo = "from core import secretos"

    p1 = ejecucion._pedir_permiso(codigo, True, Path("."), permisos)
    p2 = ejecucion._pedir_permiso(codigo, True, Path("."), permisos)

    assert p1 is None and p2 is None
    assert len(pedidos) == 1, "la segunda vez no tendria que preguntar"
    assert "credenciales" in permisos


def test_decir_no_bloquea(monkeypatch):
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda r, d, c: "no")
    bloqueo = ejecucion._pedir_permiso("from core import secretos", True,
                                       Path("."), set())
    assert bloqueo is not None
    assert bloqueo.get("rechazado_por_el_usuario") is True


def test_sin_confirmador_se_bloquea(monkeypatch):
    # Es el caso de una tarea programada corriendo sin nadie delante: no se
    # descifra a ciegas, se corta y queda en el log de la corrida.
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", None)
    ejecucion._APROBADAS_SIEMPRE.clear()
    bloqueo = ejecucion._pedir_permiso("from core import secretos", True,
                                       Path("."), set())
    assert bloqueo is not None
    assert bloqueo.get("requeria_confirmacion") is True
