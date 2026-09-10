# -*- coding: utf-8 -*-
"""
Tests del autocompletado de sitios.

Se prueba con un Tk real pero sin mostrar ventanas: lo que importa es la
detección del prefijo, el filtrado y qué queda escrito en el cuadro después de
elegir — que es el punto de todo esto (que en el prompt quede el dominio
exacto).

Si no hay display disponible, los tests se saltan en vez de fallar.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tk = pytest.importorskip("tkinter")

from core.autocompletado import CompletadorSitios  # noqa: E402

SITIOS = [
    {"sitio": "linkedin.com", "hosts": ["linkedin.com", "www.linkedin.com",
                                        "api.linkedin.com"], "flujos": 42},
    {"sitio": "google.com", "hosts": ["google.com", "accounts.google.com"],
     "flujos": 17},
    {"sitio": "phish.net", "hosts": ["google.com.ar.phish.net"], "flujos": 2},
]


@pytest.fixture
def cuadro():
    try:
        raiz = tk.Tk()
    except tk.TclError:
        pytest.skip("sin display")
    raiz.withdraw()
    texto = tk.Text(raiz)
    texto.pack()
    comp = CompletadorSitios(texto, proveedor=lambda: SITIOS)
    yield texto, comp
    comp.cerrar()
    raiz.destroy()


def _escribir(texto, contenido):
    texto.delete("1.0", "end")
    texto.insert("1.0", contenido)
    texto.mark_set("insert", "end-1c")


# --- Deteccion del prefijo -------------------------------------------------

def test_detecta_el_arroba(cuadro):
    texto, comp = cuadro
    _escribir(texto, "sacame los trabajos de @link")
    inicio, escrito = comp._prefijo_en_cursor()
    assert inicio is not None
    assert escrito == "link"


def test_sin_arroba_no_hay_prefijo(cuadro):
    texto, comp = cuadro
    _escribir(texto, "sacame los trabajos")
    assert comp._prefijo_en_cursor() == (None, None)


def test_un_espacio_despues_del_arroba_cancela(cuadro):
    texto, comp = cuadro
    # Un '@' viejo mas atras no debe capturar lo que se escribe ahora.
    _escribir(texto, "mira @linkedin.com y sacame los datos")
    assert comp._prefijo_en_cursor() == (None, None)


def test_arroba_solo_ofrece_todo(cuadro):
    texto, comp = cuadro
    _escribir(texto, "@")
    inicio, escrito = comp._prefijo_en_cursor()
    assert inicio is not None and escrito == ""


# --- Filtrado ---------------------------------------------------------------

def test_lista_dominios_y_sus_hosts(cuadro):
    _texto, comp = cuadro
    visibles = comp._candidatos("")
    # El dominio aparece con su conteo de flujos, para poder distinguir cual
    # navegaste de verdad.
    assert any("linkedin.com" in v and "42 flujos" in v for v in visibles)
    assert "linkedin.com" in comp.opciones
    assert "api.linkedin.com" in comp.opciones


def test_filtra_por_lo_escrito(cuadro):
    _texto, comp = cuadro
    comp._candidatos("google")
    assert "google.com" in comp.opciones
    assert "linkedin.com" not in comp.opciones


def test_el_lookalike_se_ve_como_lo_que_es(cuadro):
    _texto, comp = cuadro
    visibles = comp._candidatos("google")
    # google.com.ar.phish.net aparece (contiene "google"), pero la etiqueta
    # dice a que dominio pertenece de verdad, para que no pase por Google.
    assert "google.com.ar.phish.net" in comp.opciones
    etiqueta = next(v for v in visibles if "google.com.ar.phish.net" in v)
    assert "phish.net" in etiqueta
    # Y el dominio real de google sigue estando aparte.
    assert "google.com" in comp.opciones


def test_sin_coincidencias_no_hay_opciones(cuadro):
    _texto, comp = cuadro
    assert comp._candidatos("instagram") == []


def test_no_repite_el_dominio_como_host(cuadro):
    _texto, comp = cuadro
    comp._candidatos("linkedin")
    assert comp.opciones.count("linkedin.com") == 1


# --- Lo que queda escrito (el punto de todo esto) --------------------------

def test_elegir_deja_el_dominio_exacto(cuadro):
    texto, comp = cuadro
    _escribir(texto, "sacame los trabajos de @link")
    comp._al_soltar_tecla(type("E", (), {"keysym": "l"})())
    assert comp.abierto, "la lista tendria que estar abierta"

    comp._aceptar()
    resultado = texto.get("1.0", "end").strip()
    assert resultado == "sacame los trabajos de linkedin.com"
    assert "@link" not in resultado


def test_despues_de_elegir_se_cierra(cuadro):
    texto, comp = cuadro
    _escribir(texto, "@link")
    comp._al_soltar_tecla(type("E", (), {"keysym": "k"})())
    comp._aceptar()
    assert not comp.abierto


def test_enter_con_lista_abierta_lo_consume(cuadro):
    texto, comp = cuadro
    _escribir(texto, "@link")
    comp._al_soltar_tecla(type("E", (), {"keysym": "k"})())
    # True = lo consumio, o sea el mensaje NO se manda a medio completar.
    assert comp.al_enter() is True


def test_enter_sin_lista_no_lo_consume(cuadro):
    texto, comp = cuadro
    _escribir(texto, "hola")
    assert comp.al_enter() is False


def test_escape_cierra_y_lo_consume(cuadro):
    texto, comp = cuadro
    _escribir(texto, "@link")
    comp._al_soltar_tecla(type("E", (), {"keysym": "k"})())
    assert comp.al_escape() is True
    assert not comp.abierto
    assert comp.al_escape() is False


def test_las_flechas_mueven_la_seleccion(cuadro):
    texto, comp = cuadro
    _escribir(texto, "@")
    comp._al_soltar_tecla(type("E", (), {"keysym": "at"})())
    assert comp.abierto
    assert comp.al_flecha(1) is True
    assert comp.lista.curselection()[0] == 1
    assert comp.al_flecha(-1) is True
    assert comp.lista.curselection()[0] == 0
    # No se pasa del principio.
    assert comp.al_flecha(-1) is True
    assert comp.lista.curselection()[0] == 0


# --- Robustez ---------------------------------------------------------------

def test_proveedor_que_falla_no_rompe(cuadro):
    texto, _comp = cuadro
    roto = CompletadorSitios(texto, proveedor=lambda: 1 / 0)
    assert roto._candidatos("") == []
    roto.cerrar()


def test_sin_captura_no_ofrece_nada(cuadro):
    texto, _comp = cuadro
    vacio = CompletadorSitios(texto, proveedor=lambda: [])
    _escribir(texto, "@link")
    vacio._al_soltar_tecla(type("E", (), {"keysym": "k"})())
    assert not vacio.abierto
    vacio.cerrar()
