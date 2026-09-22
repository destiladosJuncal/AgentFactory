# -*- coding: utf-8 -*-
"""
Tests de la confirmación antes de instalar paquetes.

Lo que importa verificar:

  1. Instalar algo nuevo PREGUNTA (antes se instalaba sin que nadie lo viera).
  2. Decir "no" no ejecuta pip.
  3. "Permitir siempre" vale para toda la conversación: el segundo paquete ya
     no pregunta.
  4. El set de permisos es POR conversación: aprobar en una no aprueba en otra.
  5. Si el paquete ya está en el almacén, no pregunta nada (no hay instalación
     real que autorizar).
  6. Sin confirmador registrado se bloquea, no se instala a ciegas.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ejecucion, paquetes  # noqa: E402


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Almacenes vacíos y pip simulado, para no bajar nada de la red."""
    compartido = tmp_path / "compartido"
    compartido.mkdir()
    monkeypatch.setattr(paquetes, "shared_dir", lambda: compartido)

    llamadas = []

    def pip_falso(requisito, destino):
        llamadas.append(requisito)
        # Simula que quedó instalado, creando el dist-info que lee instalados().
        nombre = paquetes.normalize(requisito.split("=")[0].split("<")[0].split(">")[0])
        (Path(destino) / f"{nombre}-1.0.dist-info").mkdir(parents=True, exist_ok=True)
        return True, "ok"

    monkeypatch.setattr(paquetes, "_pip_install", pip_falso)
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", None)
    ejecucion._APROBADAS_SIEMPRE.clear()
    return llamadas


def _confirmador(respuesta, registro=None):
    def f(resumen, detalle, clave):
        if registro is not None:
            registro.append({"resumen": resumen, "detalle": detalle, "clave": clave})
        return respuesta
    return f


# --- 1 y 2: pregunta, y "no" no instala ------------------------------------

def test_instalar_algo_nuevo_pregunta(entorno, monkeypatch):
    visto = []
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", _confirmador("permitir", visto))
    permisos = set()

    r = paquetes.install("numpy", permisos=permisos)

    assert len(visto) == 1, "tendria que haber preguntado exactamente una vez"
    assert "numpy" in visto[0]["resumen"]
    assert visto[0]["clave"] == "pip"
    assert "pip install" in visto[0]["detalle"]
    assert r["estado"] == "instalado"
    assert entorno == ["numpy"]


def test_decir_no_no_ejecuta_pip(entorno, monkeypatch):
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", _confirmador("no"))
    permisos = set()

    r = paquetes.install("paquete-raro", permisos=permisos)

    assert r.get("rechazado_por_el_usuario") is True
    assert entorno == [], "pip no tendria que haberse ejecutado"


# --- 3: "siempre" vale para la conversación --------------------------------

def test_permitir_siempre_no_vuelve_a_preguntar(entorno, monkeypatch):
    visto = []
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", _confirmador("siempre", visto))
    permisos = set()

    paquetes.install("numpy", permisos=permisos)
    paquetes.install("pandas", permisos=permisos)
    paquetes.install("requests", permisos=permisos)

    assert len(visto) == 1, "solo el primero tendria que preguntar"
    assert entorno == ["numpy", "pandas", "requests"]
    assert "pip" in permisos


# --- 4: el permiso no se filtra a otra conversación ------------------------

def test_el_permiso_es_por_conversacion(entorno, monkeypatch):
    visto = []
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", _confirmador("siempre", visto))

    permisos_a, permisos_b = set(), set()
    paquetes.install("numpy", permisos=permisos_a)
    paquetes.install("pandas", permisos=permisos_b)

    assert len(visto) == 2, "la segunda conversacion tiene que preguntar de nuevo"
    assert "pip" in permisos_a and "pip" in permisos_b


# --- 5: lo que ya está no pregunta ----------------------------------------

def test_si_ya_estaba_no_pregunta(entorno, monkeypatch):
    visto = []
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", _confirmador("permitir", visto))
    permisos = set()

    paquetes.install("numpy", permisos=permisos)   # instala y pregunta
    visto.clear()
    r = paquetes.install("numpy", permisos=set())  # otra conversacion, sin permiso

    assert r["estado"] == "ya_estaba"
    assert visto == [], "reutilizar del almacen no necesita autorizacion"


# --- 6: sin confirmador, se bloquea ---------------------------------------

def test_sin_confirmador_se_bloquea(entorno, monkeypatch):
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", None)
    r = paquetes.install("numpy", permisos=set())
    assert r.get("requeria_confirmacion") is True
    assert entorno == []
