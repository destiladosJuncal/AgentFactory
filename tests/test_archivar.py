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


# --- compresión a .tar.xz al archivar -------------------------------------
import json as _json  # noqa: E402

from core.conversaciones import ARCHIVO_COMPRIMIDO  # noqa: E402


def _poblar(d):
    """Mete contenido real: mensajes.json con varios mensajes y un archivo en el
    workspace, para probar que comprimir/descomprimir no pierde nada."""
    msgs = [{"role": "user", "content": "hola " * 50},
            {"role": "assistant", "content": "qué tal " * 50}]
    (d / "mensajes.json").write_text(_json.dumps(msgs), encoding="utf-8")
    (d / "workspace" / "nota.txt").write_text("contenido " * 100, encoding="utf-8")
    return msgs


def test_comprimir_mueve_todo_al_tarxz_y_borra_originales(tmp_path):
    g = _gestor(tmp_path)
    d = g.crear_conversacion("pesada")
    _poblar(d)

    assert g.comprimir_conversacion(d.name) is True
    assert (d / ARCHIVO_COMPRIMIDO).exists()          # el paquete está
    assert not (d / "mensajes.json").exists()         # originales borrados
    assert not (d / "workspace").exists()
    assert (d / "meta.json").exists()                 # meta queda afuera, intacto

    meta = _json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["archivada"] is True and meta["comprimida"] is True
    assert meta["mensajes_n"] == 2                    # conteo cacheado


def test_descomprimir_restaura_identico(tmp_path):
    g = _gestor(tmp_path)
    d = g.crear_conversacion("ida y vuelta")
    msgs = _poblar(d)
    ws_antes = (d / "workspace" / "nota.txt").read_text(encoding="utf-8")

    g.comprimir_conversacion(d.name)
    assert g.descomprimir_conversacion(d.name) is True

    assert not (d / ARCHIVO_COMPRIMIDO).exists()      # el paquete se fue
    assert _json.loads((d / "mensajes.json").read_text(encoding="utf-8")) == msgs
    assert (d / "workspace" / "nota.txt").read_text(encoding="utf-8") == ws_antes
    meta = _json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["archivada"] is False and meta["comprimida"] is False


def test_listado_cuenta_mensajes_aunque_este_comprimida(tmp_path):
    g = _gestor(tmp_path)
    d = g.crear_conversacion("conteo")
    _poblar(d)
    g.comprimir_conversacion(d.name)

    fila = g.listar_conversaciones()[0]
    assert fila["comprimida"] is True
    assert fila["mensajes"] == 2        # del cache, sin descomprimir


def test_tarxz_pesa_menos_que_el_crudo(tmp_path):
    g = _gestor(tmp_path)
    d = g.crear_conversacion("ratio")
    _poblar(d)
    crudo = (d / "mensajes.json").stat().st_size + \
            (d / "workspace" / "nota.txt").stat().st_size

    g.comprimir_conversacion(d.name)
    comprimido = (d / ARCHIVO_COMPRIMIDO).stat().st_size
    # Texto repetitivo: xz lo deja muy por debajo del original.
    assert comprimido < crudo


def test_puente_archivar_comprime_y_suelta_cache(tmp_path):
    from webui.puente import Puente
    g = _gestor(tmp_path)
    d = g.crear_conversacion("por puente comprime")
    _poblar(d)

    pu = Puente.__new__(Puente)
    pu.gestor = g
    pu._chats = {d.name: object()}                  # cache falso: debe soltarse

    r = pu.archivar_conversacion(d.name, True)
    assert r["ok"] is True
    assert (d / ARCHIVO_COMPRIMIDO).exists()
    assert d.name not in pu._chats                  # soltó el chat cacheado

    # Desarchivar restaura.
    assert pu.archivar_conversacion(d.name, False)["ok"] is True
    assert not (d / ARCHIVO_COMPRIMIDO).exists()
    assert (d / "mensajes.json").exists()
