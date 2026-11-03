"""Tests de la bóveda de secretos (core.boveda) + sustitución en ejecución.

El agente nunca ve el valor: pide por etiqueta (ventana), se guarda cifrado, y
solo se usa vía {{secreto:ETIQUETA}} sustituido en el borde del subprocess, con
aprobación. Usa tmp_path como datos; no toca nada real.
"""

import pytest


@pytest.fixture
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTE_DATOS", str(tmp_path))
    from core import secretos
    secretos.forget()
    import importlib, core.rutas
    importlib.reload(core.rutas)
    yield tmp_path
    secretos.forget()


def test_guardar_listar_borrar(datos):
    from core import boveda
    assert boveda.etiquetas() == []
    r = boveda.guardar("GITHUB_TOKEN", "ghp_secreto123")
    assert r["ok"] and boveda.tiene("GITHUB_TOKEN")
    assert boveda.etiquetas() == ["GITHUB_TOKEN"]
    # el valor en disco NO está en claro (si hay cripto) y _valor lo recupera
    assert boveda._valor("GITHUB_TOKEN") == "ghp_secreto123"
    assert boveda.borrar("GITHUB_TOKEN")["ok"]
    assert boveda.etiquetas() == []


def test_etiqueta_invalida(datos):
    from core import boveda
    assert boveda.guardar("mal nombre!", "x")["ok"] is False


def test_placeholders_y_sustitucion(datos):
    from core import boveda
    boveda.guardar("TOKEN", "VALOR-REAL")
    cmd = 'curl -H "Authorization: Bearer {{secreto:TOKEN}}" api'
    assert boveda.etiquetas_en(cmd) == ["TOKEN"]
    real, usadas, faltan = boveda.sustituir(cmd)
    assert "VALOR-REAL" in real and usadas == ["TOKEN"] and faltan == []
    # faltante: se deja el placeholder y se reporta
    real2, _u, faltan2 = boveda.sustituir("echo {{secreto:NOPE}}")
    assert faltan2 == ["NOPE"] and "{{secreto:NOPE}}" in real2


def test_pedir_sin_solicitador_y_con_solicitador(datos):
    from core import boveda
    boveda.registrar_solicitador(None)
    assert boveda.pedir("NUEVA", "para X")["ok"] is False      # no hay forma de pedir
    boveda.registrar_solicitador(lambda etiqueta, motivo: "valor-ingresado")
    r = boveda.pedir("NUEVA", "para X")
    assert r["ok"] and r["etiqueta"] == "NUEVA" and "valor" not in r   # NO devuelve el valor
    assert boveda._valor("NUEVA") == "valor-ingresado"
    # ya existe -> no re-pregunta
    boveda.registrar_solicitador(lambda e, m: (_ for _ in ()).throw(AssertionError("no debía pedir")))
    assert boveda.pedir("NUEVA")["ya_existe"] is True
    boveda.registrar_solicitador(None)


def test_redactor_tacha_valores_de_boveda(datos):
    from core import boveda, redactor
    boveda.guardar("API", "sk-supersecreto-xyz")
    out = redactor.redactar("el resultado fue sk-supersecreto-xyz listo")
    assert "sk-supersecreto-xyz" not in out and redactor.MARCA in out


def test_ejecucion_sustituye_con_aprobacion(datos, monkeypatch):
    from core import boveda, ejecucion
    boveda.guardar("TOKEN", "VALOR-REAL")
    # confirmador que aprueba
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda resumen, detalle, clave: "permitir")
    real, err = ejecucion._preparar_secretos('echo {{secreto:TOKEN}}')
    assert err is None and real == "echo VALOR-REAL"
    # confirmador que niega
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda r, d, c: "no")
    _real, err2 = ejecucion._preparar_secretos('echo {{secreto:TOKEN}}')
    assert err2 and "No autorizaste" in err2["error"]
    # sin placeholders -> passthrough
    assert ejecucion._preparar_secretos("ls -la") == ("ls -la", None)
    # faltante -> error, sin pedir confirmación
    monkeypatch.setattr(ejecucion, "CONFIRMADOR", lambda r, d, c: "permitir")
    _r, err3 = ejecucion._preparar_secretos("echo {{secreto:NOPE}}")
    assert err3 and "no cargado" in err3["error"]
