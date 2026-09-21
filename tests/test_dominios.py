# -*- coding: utf-8 -*-
"""
Tests de core/dominios.py.

Los casos no son inventados: son exactamente los que la comparación por
subcadena confundía. Cada uno de los `no pertenece` de acá era un dominio que
`host LIKE '%sitio%'` metía en los resultados como si fuera el sitio pedido.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import dominios  # noqa: E402


# --- normalizar -------------------------------------------------------------

def test_normalizar_acepta_url_entera():
    # El modelo manda una URL con la misma naturalidad que un host.
    assert dominios.normalize("https://www.linkedin.com/jobs") == "www.linkedin.com"
    assert dominios.normalize("linkedin.com:443") == "linkedin.com"
    assert dominios.normalize("  LinkedIn.COM.  ") == "linkedin.com"
    assert dominios.normalize("user:pass@linkedin.com") == "linkedin.com"


def test_normalizar_vacio():
    assert dominios.normalize("") == ""
    assert dominios.normalize(None) == ""


# --- registrable ------------------------------------------------------------

def test_registrable_saca_subdominios():
    assert dominios.registrable("accounts.google.com") == "google.com"
    assert dominios.registrable("www.linkedin.com") == "linkedin.com"


def test_registrable_no_se_deja_enganar_por_lookalikes():
    # El caso que motiva usar la Public Suffix List: contar puntos no alcanza.
    assert dominios.registrable("google.com.ar.phish.net") == "phish.net"
    assert dominios.registrable("notlinkedin.com") == "notlinkedin.com"


def test_registrable_con_ip():
    assert dominios.registrable("127.0.0.1") == "127.0.0.1"


# --- pertenece --------------------------------------------------------------

def test_pertenece_exacto_y_subdominio():
    assert dominios.belongs_to("linkedin.com", "linkedin.com")
    assert dominios.belongs_to("www.linkedin.com", "linkedin.com")
    assert dominios.belongs_to("a.b.linkedin.com", "linkedin.com")


def test_pertenece_rechaza_lo_que_la_subcadena_confundia():
    assert not dominios.belongs_to("notlinkedin.com", "linkedin.com")
    assert not dominios.belongs_to("linkedin.evil.com", "linkedin.com")
    assert not dominios.belongs_to("phish-linkedin.ru", "linkedin.com")
    assert not dominios.belongs_to("mi-linkedin-scraper.io", "linkedin.com")
    assert not dominios.belongs_to("google.com.ar.phish.net", "google.com")


def test_pertenece_no_confunde_sufijo_sin_punto():
    # 'evillinkedin.com' termina en 'linkedin.com' como TEXTO, pero no es
    # un subdominio: hace falta el punto separador.
    assert not dominios.belongs_to("evillinkedin.com", "linkedin.com")


def test_pertenece_vacios():
    assert not dominios.belongs_to("", "linkedin.com")
    assert not dominios.belongs_to("linkedin.com", "")


# --- resolver ---------------------------------------------------------------

HOSTS = [
    "linkedin.com", "www.linkedin.com", "media.licdn.com",
    "linkedin.evil.com", "notlinkedin.com", "mi-linkedin-scraper.io",
    "phish-linkedin.ru", "google.com", "accounts.google.com",
    "google.com.ar.phish.net",
]


def test_resolver_dominio_completo_trae_solo_lo_suyo():
    r = dominios.resolve(HOSTS, "linkedin.com")
    assert r["sitio"] == "linkedin.com"
    assert r["hosts"] == ["linkedin.com", "www.linkedin.com"]
    assert "notlinkedin.com" not in r["hosts"]


def test_resolver_google_no_arrastra_el_lookalike():
    r = dominios.resolve(HOSTS, "google.com")
    assert r["hosts"] == ["accounts.google.com", "google.com"]
    assert "google.com.ar.phish.net" not in r["hosts"]


def test_resolver_palabra_suelta_ambigua_no_adivina():
    # 'linkedin' como palabra: hay linkedin.com y otros dominios cuyo nombre
    # registrable ES 'linkedin' en otra parte? No: solo linkedin.com califica.
    r = dominios.resolve(HOSTS, "linkedin")
    assert r.get("sitio") == "linkedin.com", r


def test_resolver_palabra_suelta_no_matchea_por_subcadena():
    # Lo importante: 'linkedin' NO trae phish-linkedin.ru ni notlinkedin.com,
    # que es lo que hoy pasa. Se compara contra las etiquetas del dominio.
    r = dominios.resolve(HOSTS, "linkedin")
    assert "phish-linkedin.ru" not in r.get("hosts", [])
    assert "notlinkedin.com" not in r.get("hosts", [])


def test_resolver_ambiguo_devuelve_candidatos():
    hosts = ["acme.com", "acme.com.ar", "acme.io"]
    r = dominios.resolve(hosts, "acme")
    assert "sitio" not in r
    assert [c["sitio"] for c in r["candidatos"]] == ["acme.com", "acme.com.ar", "acme.io"]


def test_resolver_sin_coincidencias_ofrece_la_lista():
    r = dominios.resolve(HOSTS, "instagram.com")
    assert r["candidatos"] == []
    assert "linkedin.com" in r["sitios"]
    assert "phish.net" in r["sitios"]


def test_resolver_vacio():
    r = dominios.resolve(HOSTS, "")
    assert r["candidatos"] == []
    assert r["sitios"]


# --- sitios_de --------------------------------------------------------------

def test_sitios_de_agrupa_por_dominio_registrable():
    s = dominios.sites_of(["www.linkedin.com", "linkedin.com", "accounts.google.com"])
    assert s == ["google.com", "linkedin.com"]
