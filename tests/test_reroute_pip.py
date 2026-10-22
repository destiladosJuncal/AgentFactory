"""Tests del rerouteo de 'pip install' por shell hacia el almacén compartido.

El agente a veces corre `pip install X` por shell en vez de usar la tool
`instalar_paquete`. Eso se salteaba el almacén compartido y reinstalaba los
mismos paquetes en cada conversación. `reroutear_pip_install` intercepta las
formas simples y las manda por `install()` (que deduplica); las formas complejas
(-e, -r, URLs, pipes) devuelven None y siguen al shell normal.
"""

from core import paquetes


def test_instala_simple_se_rutea(monkeypatch):
    vistos = []

    def fake_install(req, workspace=None, permisos=None):
        vistos.append(req)
        return {"estado": "ya_estaba", "donde": "compartida", "paquete": req}

    monkeypatch.setattr(paquetes, "install", fake_install)
    r = paquetes.reroutear_pip_install("pip install numpy pandas")
    assert r is not None and r["rerouteado_al_almacen"] is True
    assert vistos == ["numpy", "pandas"]
    assert len(r["resultados"]) == 2


def test_prefijos_aceptados(monkeypatch):
    monkeypatch.setattr(paquetes, "install",
                        lambda req, workspace=None, permisos=None: {"estado": "instalado", "paquete": req})
    for cmd in ("pip install requests",
                "pip3 install requests",
                "python -m pip install requests",
                "python3 -m pip install requests"):
        assert paquetes.reroutear_pip_install(cmd) is not None, cmd


def test_flags_benignos_se_ignoran(monkeypatch):
    vistos = []
    monkeypatch.setattr(paquetes, "install",
                        lambda req, workspace=None, permisos=None: vistos.append(req) or {"estado": "instalado"})
    r = paquetes.reroutear_pip_install("pip install --quiet --no-cache-dir mitmproxy")
    assert r is not None
    assert vistos == ["mitmproxy"]


def test_formas_complejas_no_se_rutean(monkeypatch):
    # Si install() llegara a llamarse acá, el test debe fallar.
    monkeypatch.setattr(paquetes, "install",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debía rutear")))
    complejos = [
        "pip install -e .",                      # editable
        "pip install -r requirements.txt",       # requirements file
        "pip install -U numpy",                  # upgrade explícito
        "pip install --target /tmp/x numpy",     # destino propio
        "pip install ./dist/paquete.whl",        # archivo local
        "pip install https://example.com/x.tar.gz",  # URL
        "pip install numpy && python run.py",    # encadenado
        "pip install numpy | tee log",           # pipe
        "pip uninstall numpy",                   # no es install
        "pip list",                              # no es install
        "echo pip install numpy",                # no arranca con pip
        "pip install",                           # sin paquetes
    ]
    for cmd in complejos:
        assert paquetes.reroutear_pip_install(cmd) is None, cmd
