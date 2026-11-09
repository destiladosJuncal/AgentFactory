# -*- coding: utf-8 -*-
"""Conector Grafana: lectura, escritura con guard de confirmación, y el registro
de conectores. El HTTP sale por un seam inyectable, así no se toca la red."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.conectores.grafana as g          # noqa: E402
from core import conectores, ejecucion        # noqa: E402


class FakeHTTP:
    """Reemplaza el seam _HTTP: registra las llamadas y devuelve lo programado."""
    def __init__(self, respuesta=(200, {"ok": True})):
        self.respuesta = respuesta
        self.llamadas = []

    def __call__(self, method, url, headers, body):
        self.llamadas.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.respuesta


def _configurar(monkeypatch, url="https://grafana.example.com", token="tok-123"):
    monkeypatch.setenv("GRAFANA_URL", url)
    monkeypatch.setenv("GRAFANA_TOKEN", token)


def test_no_configurado_si_falta_credencial(monkeypatch):
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    monkeypatch.delenv("GRAFANA_TOKEN", raising=False)
    assert g.esta_configurado() is False
    # Sin credenciales, una tool de lectura avisa en vez de pegarle a la red.
    r = g.buscar("cpu")
    assert "error" in r and "conect" in r["error"].lower()


def test_buscar_arma_la_url_y_el_bearer(monkeypatch):
    _configurar(monkeypatch)
    fake = FakeHTTP((200, [{"uid": "abc", "title": "CPU"}]))
    monkeypatch.setattr(g, "_HTTP", fake)

    r = g.buscar("cpu", tipo="dash-db", limite=5)
    assert r["ok"] is True and r["data"][0]["uid"] == "abc"
    llamada = fake.llamadas[0]
    assert llamada["method"] == "GET"
    assert llamada["url"] == "https://grafana.example.com/api/search?limit=5&query=cpu&type=dash-db"
    assert llamada["headers"]["Authorization"] == "Bearer tok-123"


def test_error_credencial_se_traduce(monkeypatch):
    _configurar(monkeypatch)
    monkeypatch.setattr(g, "_HTTP", FakeHTTP((401, {"message": "Unauthorized"})))
    r = g.datasources()
    assert "error" in r and "401" in r["error"]


def test_query_exige_queries(monkeypatch):
    _configurar(monkeypatch)
    monkeypatch.setattr(g, "_HTTP", FakeHTTP((200, {})))
    assert "error" in g.query({})           # sin 'queries'
    assert "error" in g.query("no es dict")


def test_escritura_sin_confirmador_no_escribe(monkeypatch):
    _configurar(monkeypatch)
    fake = FakeHTTP((200, {"id": 1}))
    monkeypatch.setattr(g, "_HTTP", fake)
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", None)   # nadie puede aprobar

    r = g.crear_anotacion("deploy v2", tags=["deploy"])
    assert "error" in r and "confirmaci" in r["error"].lower()
    assert fake.llamadas == []               # no se llamó a la red


def test_escritura_con_aprobacion_si_escribe(monkeypatch):
    _configurar(monkeypatch)
    fake = FakeHTTP((200, {"id": 42, "message": "Annotation added"}))
    monkeypatch.setattr(g, "_HTTP", fake)
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda resumen, detalle, clave: "permitir")

    r = g.crear_anotacion("deploy v2", tags=["deploy"], dashboard_uid="abc")
    assert r["ok"] is True and r["data"]["id"] == 42
    llamada = fake.llamadas[0]
    assert llamada["method"] == "POST"
    assert llamada["url"].endswith("/api/annotations")
    assert llamada["body"]["text"] == "deploy v2"
    assert llamada["body"]["dashboardUID"] == "abc"


def test_escritura_rechazada_no_escribe(monkeypatch):
    _configurar(monkeypatch)
    fake = FakeHTTP((200, {}))
    monkeypatch.setattr(g, "_HTTP", fake)
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda r, d, c: "no")

    r = g.guardar_dashboard({"title": "Nuevo"}, overwrite=True)
    assert "error" in r and "Cancelado" in r["error"]
    assert fake.llamadas == []


# -- registro de conectores --------------------------------------------------

def test_registro_solo_expone_configurados(monkeypatch):
    # Sin credenciales: Grafana no aparece en el esquema que ve el modelo.
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    monkeypatch.delenv("GRAFANA_TOKEN", raising=False)
    assert conectores.nombres_conectores() == set()
    assert conectores.esquema_conectores() == []

    # Con credenciales: aparecen sus tools, todas con prefijo grafana_.
    _configurar(monkeypatch)
    nombres = conectores.nombres_conectores()
    assert "grafana_buscar" in nombres and "grafana_crear_anotacion" in nombres
    assert all(n.startswith("grafana_") for n in nombres)


def test_registro_despacha_por_nombre(monkeypatch):
    _configurar(monkeypatch)
    monkeypatch.setattr(g, "_HTTP", FakeHTTP((200, [{"uid": "x"}])))
    r = conectores.ejecutar_tool_conector("grafana_buscar", {"query": "net"})
    assert r["ok"] is True
    assert "error" in conectores.ejecutar_tool_conector("tool_que_no_existe", {})


def test_estado_todos_incluye_grafana(monkeypatch):
    _configurar(monkeypatch)
    estado = conectores.estado_todos()
    gra = next(c for c in estado if c["id"] == "grafana")
    assert gra["nombre"] == "Grafana" and gra["configurado"] is True


def test_solicitar_confirmacion_rechaza_sin_confirmador(monkeypatch):
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", None)
    assert conectores.solicitar_confirmacion("hacer algo") is False
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda r, d, c: "siempre")
    assert conectores.solicitar_confirmacion("hacer algo") is True
