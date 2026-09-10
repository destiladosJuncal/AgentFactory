"""
Tests del preprocesador de preguntas.

Correr:  cd <instalación> && ./venv/bin/python -m pytest tests/test_procesador.py -v
         (o simplemente: ./venv/bin/python tests/test_procesador.py)

Todo lo que se prueba acá corre SIN API: la descomposición por reglas es
determinista, y para el envío en paralelo se usan proveedores falsos. Así los
tests son rápidos, gratis y reproducibles.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.procesador import (  # noqa: E402
    CONECTORES_ES,
    ErrorProcesador,
    Resultado,
    _idioma,
    descomponer,
    descomponer_local,
    procesar,
    responder,
)

EJEMPLO = "¿Cómo se configura Nginx con SSL, y cómo se renuevan los certificados?"


# --- Requisito 2: salida List[str], mínimo 6 --------------------------------

def test_devuelve_lista_de_strings():
    salida = descomponer_local(EJEMPLO)
    assert isinstance(salida, list)
    assert salida and all(isinstance(p, str) for p in salida)


def test_no_devuelve_json_ni_diccionarios():
    for p in descomponer_local(EJEMPLO):
        assert not isinstance(p, (dict, list))
        assert not p.strip().startswith(("{", "["))


def test_minimo_seis_preguntas():
    assert len(descomponer_local(EJEMPLO)) >= 6


def test_minimo_seis_incluso_con_texto_trivial():
    """Requisito: si el texto no da 6, se generan artificialmente."""
    assert len(descomponer_local("¿Qué es Docker?")) >= 6


def test_minimo_configurable():
    assert len(descomponer_local(EJEMPLO, minimo=10)) >= 10


# --- Requisito 3: conectores lógicos ----------------------------------------

@pytest.mark.parametrize("conector", [
    "y", "para", "usando", "también", "además", "mientras", "cuando",
    "porque", "mediante", "a través de", "por medio de", "siempre que",
    "dado que", "ya que",
])
def test_reconoce_cada_conector(conector):
    """Cada conector de la lista tiene que producir un corte cuando lo que
    queda a ambos lados es una cláusula completa."""
    texto = (f"¿Cómo se despliega la aplicación en Kubernetes {conector} "
             f"se configuran las réplicas del servicio?")
    preguntas = descomponer_local(texto)
    assert any("despliega" in p.lower() for p in preguntas)
    assert any("réplicas" in p.lower() or "configuran" in p.lower() for p in preguntas)


def test_conectores_declarados_estan_todos():
    for c in ["y", "para", "usando", "con", "también", "además", "mientras",
              "cuando", "si", "porque", "mediante", "a través de",
              "por medio de", "siempre que", "dado que", "ya que"]:
        assert c in CONECTORES_ES, f"falta el conector '{c}'"


# --- Requisito 4: independencia gramatical ----------------------------------

def test_cada_pregunta_es_una_pregunta():
    for p in descomponer_local(EJEMPLO):
        assert p.rstrip().endswith("?"), f"no termina en '?': {p}"
        assert len(p.split()) >= 2, f"demasiado corta para sostenerse sola: {p}"


def test_no_hay_referencias_colgadas():
    """Una sub-pregunta no puede empezar con un conector suelto."""
    for p in descomponer_local(EJEMPLO):
        primera = p.lstrip("¿").split()[0].lower()
        assert primera not in {"y", "pero", "también", "además", "mientras"}


def test_no_se_pierde_informacion_al_partir():
    """'con SSL' es un complemento, no una cláusula: no se descarta."""
    preguntas = descomponer_local(EJEMPLO)
    assert any("ssl" in p.lower() for p in preguntas)
    assert any("nginx" in p.lower() for p in preguntas)
    assert any("certificado" in p.lower() for p in preguntas)


def test_sin_duplicados():
    preguntas = descomponer_local(
        "¿Cómo se instala Redis y cómo se instala Redis?")
    normalizadas = [p.lower().strip("¿?") for p in preguntas]
    assert len(normalizadas) == len(set(normalizadas))


# --- Requisito 5: español e inglés ------------------------------------------

def test_detecta_idioma():
    assert _idioma(EJEMPLO) == "es"
    assert _idioma("How do you configure Nginx with SSL and renew certificates?") == "en"


def test_funciona_en_ingles():
    texto = "How do you configure Nginx with SSL, and how are the certificates renewed?"
    preguntas = descomponer_local(texto)
    assert len(preguntas) >= 6
    assert all(p.endswith("?") for p in preguntas)
    # En inglés no se abren signos de interrogación.
    assert not any(p.startswith("¿") for p in preguntas)


def test_espanol_usa_signo_de_apertura():
    assert all(p.startswith("¿") for p in descomponer_local(EJEMPLO))


# --- Requisito 1: límites de entrada ----------------------------------------

def test_acepta_textos_largos_sin_tope_de_500():
    """El límite viejo de 500 palabras ya no existe: se trocea."""
    texto = "¿Cómo se configura el servidor de aplicaciones? " * 300   # ~2100 palabras
    preguntas = descomponer_local(texto)
    assert len(preguntas) >= 6


def test_rechaza_solo_lo_absurdamente_largo():
    from core.procesador import MAX_TROZOS, PALABRAS_POR_TROZO
    with pytest.raises(ErrorProcesador, match="tope"):
        descomponer_local("palabra " * (PALABRAS_POR_TROZO * MAX_TROZOS + 10))


def test_trocear_nunca_corta_una_oracion_al_medio():
    from core.procesador import trocear
    texto = ("¿Cómo se despliega el servicio en produccion? "
             "¿Qué pasa si falla el health check durante el arranque? ") * 60
    trozos = trocear(texto)
    assert len(trozos) > 1, "un texto largo debería partirse"
    for t in trozos:
        assert t.rstrip().endswith(("?", ".", "!")), f"trozo cortado al medio: …{t[-40:]!r}"


def test_trocear_no_pierde_texto():
    from core.procesador import trocear
    texto = "Primera oración. Segunda oración larga con más palabras. Tercera."
    assert "".join(trocear(texto)).replace(" ", "") == texto.replace(" ", "")


def test_trocear_texto_corto_no_lo_parte():
    from core.procesador import trocear
    assert len(trocear(EJEMPLO)) == 1


def test_texto_largo_se_descompone_por_trozos_en_paralelo(monkeypatch):
    """Cada trozo va al LLM por separado; el mínimo se aplica al total."""
    import core.procesador as proc

    class PorTrozo:
        nombre = "falso"
        def __init__(self): self.llamadas = 0
        def completar(self, mensajes, **_kwargs):
            from core.proveedores import Respuesta
            self.llamadas += 1
            n = self.llamadas
            return Respuesta(texto=f"¿Pregunta {n} A?\n¿Pregunta {n} B?")

    p = PorTrozo()
    texto = ("¿Cómo se configura el balanceador de carga en produccion? "
             "¿Qué métricas conviene monitorear durante el despliegue? ") * 60
    preguntas = proc.descomponer(texto, proveedor=p)
    assert p.llamadas > 1, "un texto largo debe generar varias llamadas (una por trozo)"
    assert len(preguntas) >= 6
    assert len(preguntas) == len(set(preguntas)), "no debe haber duplicados entre trozos"


@pytest.mark.parametrize("entrada", ["", "   ", "\n\t"])
def test_rechaza_texto_vacio(entrada):
    with pytest.raises(ErrorProcesador):
        descomponer_local(entrada)


# --- Fallback: descomponer() sin proveedor ----------------------------------

def test_descomponer_sin_api_cae_a_las_reglas():
    salida = descomponer(EJEMPLO, proveedor=None)
    assert isinstance(salida, list) and len(salida) >= 6


class _ProveedorRoto:
    nombre = "roto"

    def completar(self, **_kwargs):
        from core.proveedores import ErrorProveedor
        raise ErrorProveedor("503 sin servicio")


def test_si_el_llm_falla_igual_devuelve_preguntas():
    salida = descomponer(EJEMPLO, proveedor=_ProveedorRoto())
    assert len(salida) >= 6


class _ProveedorTacano:
    """Devuelve menos preguntas que el mínimo: hay que completar."""
    nombre = "tacano"

    def completar(self, **_kwargs):
        from core.proveedores import Respuesta
        return Respuesta(texto="¿Cómo se configura Nginx?\n¿Cómo se renuevan los certificados?")


def test_completa_hasta_el_minimo_si_el_llm_devuelve_pocas():
    salida = descomponer(EJEMPLO, proveedor=_ProveedorTacano())
    assert len(salida) >= 6


class _ProveedorCharlatan:
    """Mete numeración y preámbulo, como hacen los modelos en la práctica."""
    nombre = "charlatan"

    def completar(self, **_kwargs):
        from core.proveedores import Respuesta
        return Respuesta(texto=(
            "Aquí están:\n"
            "1. ¿Cómo se configura Nginx con SSL?\n"
            "2. ¿Cómo se renuevan los certificados?\n"
            "- ¿Qué es SSL?\n"
            "* ¿Cómo se instala Nginx?\n"
            '"¿Cómo se genera un certificado?"\n'
            "6) ¿Cómo se valida un certificado?\n"))


def test_limpia_numeracion_y_vinetas():
    salida = descomponer(EJEMPLO, proveedor=_ProveedorCharlatan())
    assert len(salida) >= 6
    for p in salida:
        assert not p[0].isdigit()
        assert not p.startswith(("-", "*", '"', "'"))
    assert "¿Cómo se configura Nginx con SSL?" in salida


# --- Envío en paralelo ------------------------------------------------------

class _ProveedorLento:
    def __init__(self, nombre, demora=0.3):
        self.nombre = nombre
        self.demora = demora
        self.recibidas = []

    def completar(self, mensajes, **_kwargs):
        from core.proveedores import Respuesta
        time.sleep(self.demora)
        self.recibidas.append(mensajes[-1]["content"])
        return Respuesta(texto=f"respuesta de {self.nombre}")


def test_el_envio_es_realmente_paralelo(monkeypatch):
    """6 preguntas de 0.3s cada una: en serie serían 1.8s. En paralelo, ~0.3s."""
    import core.procesador as proc
    lento = _ProveedorLento("deepseek")
    monkeypatch.setattr(proc, "crear_proveedor", lambda n=None: lento)

    preguntas = [f"¿Pregunta {i}?" for i in range(6)]
    inicio = time.time()
    respuestas = responder(preguntas, modo="deepseek", max_hilos=6)
    transcurrido = time.time() - inicio

    assert len(respuestas) == 6
    assert transcurrido < 1.0, f"tardó {transcurrido:.1f}s: no se paralelizó"


def test_respeta_el_orden_de_las_preguntas(monkeypatch):
    import core.procesador as proc
    monkeypatch.setattr(proc, "crear_proveedor", lambda n=None: _ProveedorLento("ds", 0.01))
    preguntas = [f"¿Pregunta {i}?" for i in range(6)]
    respuestas = responder(preguntas, modo="deepseek")
    assert [r.pregunta for r in respuestas] == preguntas


def test_modo_combinado_reparte_entre_los_dos(monkeypatch):
    import core.procesador as proc
    ds, cl = _ProveedorLento("deepseek", 0.01), _ProveedorLento("claude", 0.01)
    monkeypatch.setattr(proc, "crear_proveedor",
                        lambda n=None: ds if n == "deepseek" else cl)

    respuestas = responder([f"¿P{i}?" for i in range(6)], modo="combinado")
    usados = {r.proveedor for r in respuestas}
    assert usados == {"deepseek", "claude"}
    assert len(respuestas) == 6, "combinado NO debe duplicar llamadas"


def test_modo_comparar_manda_cada_pregunta_a_los_dos(monkeypatch):
    import core.procesador as proc
    ds, cl = _ProveedorLento("deepseek", 0.01), _ProveedorLento("claude", 0.01)
    monkeypatch.setattr(proc, "crear_proveedor",
                        lambda n=None: ds if n == "deepseek" else cl)

    preguntas = [f"¿P{i}?" for i in range(3)]
    respuestas = responder(preguntas, modo="comparar")
    assert len(respuestas) == 6, "comparar debe consultar a los dos por pregunta"


def test_un_proveedor_caido_no_tumba_el_lote(monkeypatch):
    import core.procesador as proc

    class Intermitente:
        nombre = "intermitente"
        def __init__(self): self.n = 0
        def completar(self, **_kwargs):
            from core.proveedores import ErrorProveedor, Respuesta
            self.n += 1
            if self.n % 2:
                raise ErrorProveedor("timeout")
            return Respuesta(texto="ok")

    monkeypatch.setattr(proc, "crear_proveedor", lambda n=None: Intermitente())
    respuestas = responder([f"¿P{i}?" for i in range(6)], modo="deepseek")
    assert len(respuestas) == 6
    assert any(r.error for r in respuestas) and any(not r.error for r in respuestas)


def test_modo_desconocido_avisa():
    with pytest.raises(ErrorProcesador, match="Modo desconocido"):
        responder(["¿P?"], modo="gpt")


# --- Pipeline completo ------------------------------------------------------

def test_procesar_solo_descomponer_no_llama_a_la_ia(monkeypatch):
    import core.procesador as proc
    monkeypatch.setattr(proc, "crear_proveedor", lambda n=None: None)
    r = procesar(EJEMPLO, solo_descomponer=True)
    assert isinstance(r, Resultado)
    assert len(r.preguntas) >= 6
    assert r.respuestas == []


def test_procesar_completo(monkeypatch):
    import core.procesador as proc
    monkeypatch.setattr(proc, "crear_proveedor", lambda n=None: _ProveedorLento("deepseek", 0.01))
    r = procesar(EJEMPLO, modo="deepseek")
    assert len(r.respuestas) == len(r.preguntas)
    assert not r.fallidas
    assert "sub-preguntas" in r.como_texto()


# --- Ejemplo de uso ejecutable ---------------------------------------------

if __name__ == "__main__":
    print("Entrada:", EJEMPLO, "\n")
    for i, p in enumerate(descomponer_local(EJEMPLO), 1):
        print(f"  {i}. {p}")
    print("\nAhora los tests:\n")
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
