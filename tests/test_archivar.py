# -*- coding: utf-8 -*-
"""Archivar/desarchivar conversaciones: un flag reversible en meta.json que las
saca de la lista principal sin borrarlas."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.conversaciones import GestorConversaciones  # noqa: E402


def _gestor(tmp_path):
    return GestorConversaciones(tmp_path / "_conversaciones")


def test_conversacion_nueva_no_esta_archivada(tmp_path):
    g = _gestor(tmp_path)
    g.crear_conversacion("hola")
    c = g.listar_conversaciones()[0]
    assert c["archivada"] is False


def test_marcar_archivada_ida_y_vuelta(tmp_path):
    g = _gestor(tmp_path)
    d = g.crear_conversacion("hola")
    nombre = d.name

    assert g.marcar_archivada(nombre, True) is True
    assert g.listar_conversaciones()[0]["archivada"] is True
    # Reversible: no se pierde nada, solo se despeja la lista.
    assert g.marcar_archivada(nombre, False) is True
    assert g.listar_conversaciones()[0]["archivada"] is False


def test_marcar_archivada_conserva_el_resto_del_meta(tmp_path):
    import json
    g = _gestor(tmp_path)
    d = g.crear_conversacion("tiene titulo")
    g.marcar_archivada(d.name, True)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["archivada"] is True
    assert meta["titulo"] == "tiene titulo"      # no pisó lo anterior
    assert "creado" in meta


def test_marcar_archivada_inexistente(tmp_path):
    g = _gestor(tmp_path)
    assert g.marcar_archivada("no-existe", True) is False


def test_puente_pasa_el_flag_y_lo_togglea(tmp_path):
    from webui.puente import Puente
    g = _gestor(tmp_path)
    d = g.crear_conversacion("desde puente")

    pu = Puente.__new__(Puente)    # sin __init__: no registra hooks
    pu.gestor = g
    pu._chats = {}

    # Por defecto aparece y no archivada.
    fila = next(c for c in pu.listar_conversaciones() if c["nombre"] == d.name)
    assert fila["archivada"] is False

    r = pu.archivar_conversacion(d.name, True)
    assert r == {"ok": True, "archivada": True}
    fila = next(c for c in pu.listar_conversaciones() if c["nombre"] == d.name)
    assert fila["archivada"] is True

    assert pu.archivar_conversacion(d.name, False)["archivada"] is False
