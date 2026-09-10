"""
Comparación de dominios: un solo lugar que decide si dos hosts son el mismo sitio.

Existe por un bug concreto. La herramienta que el agente usa para buscar en la
captura filtraba con `host LIKE '%sitio%'`, o sea por subcadena. Eso hacía dos
cosas mal a la vez:

    sitio='linkedin.com'  ->  tambien traia  notlinkedin.com
    sitio='google.com'    ->  tambien traia  google.com.ar.phish.net
    sitio='linkedin'      ->  traia 6 hosts, entre ellos phish-linkedin.ru

El primer daño es de correctitud: datos de dominios ajenos mezclados en la
extracción, en silencio. El segundo es de seguridad: contenido de un dominio
parecido —o directamente de un lookalike— llega al modelo presentado como si
fuera del sitio real.

Lo llamativo es que la app ya sabía hacerlo bien en otro lado: el filtro de
captura (`core/proxy.py:_interesa`) compara con `host == h or
host.endswith("." + h)`, que es lo correcto. Este módulo generaliza esa regla
y la pone donde todos puedan usarla.

Para saber qué parte de un host es el dominio que alguien registró se usa la
Public Suffix List, vía publicsuffix2 (ya viene con mitmproxy). Sin ella no se
puede distinguir 'google.com.ar' (registrable) de 'phish.net' en
'google.com.ar.phish.net': hace falta la lista real de sufijos públicos, no se
puede deducir contando puntos.
"""

from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit


def _sin_esquema(texto: str) -> str:
    """Acepta que le pasen una URL entera y no solo el host.

    El modelo manda 'https://linkedin.com/jobs' con la misma naturalidad que
    'linkedin.com', y rechazarlo sería puntilloso sin motivo."""
    texto = (texto or "").strip().strip("<>\"'")
    if "//" in texto:
        texto = urlsplit(texto if "://" in texto else "http://" + texto).netloc
    texto = texto.split("/")[0]
    if "@" in texto:                      # user:pass@host
        texto = texto.rsplit("@", 1)[1]
    if texto.startswith("["):             # IPv6 entre corchetes
        return texto.split("]")[0] + "]"
    return texto.split(":")[0]            # saca el puerto


def normalizar(host: str) -> str:
    """Host en minúsculas, sin punto final, sin esquema ni puerto."""
    return _sin_esquema(host).lower().rstrip(".").lstrip(".")


def registrable(host: str) -> str:
    """El dominio que alguien registró: 'accounts.google.com' -> 'google.com'.

    Devuelve el host normalizado si no se puede determinar (una IP, un host de
    una sola etiqueta, o si la biblioteca no está)."""
    h = normalizar(host)
    if not h or h.replace(".", "").isdigit() or h.startswith("["):
        return h                          # IP: no tiene dominio registrable
    try:
        from publicsuffix2 import get_sld
        sld = get_sld(h)
        return (sld or h).lower()
    except Exception:
        # Sin publicsuffix2 se degrada a las dos últimas etiquetas. Es peor
        # (no distingue 'com.ar') pero sigue siendo mucho mejor que subcadena.
        partes = h.split(".")
        return ".".join(partes[-2:]) if len(partes) >= 2 else h


def pertenece(host: str, sitio: str) -> bool:
    """¿`host` es `sitio` o un subdominio suyo?

    Es la regla de core/proxy.py:_interesa: igualdad exacta o sufijo con punto.
    'notlinkedin.com' NO pertenece a 'linkedin.com', que es justamente lo que
    la comparación por subcadena no distinguía."""
    h, s = normalizar(host), normalizar(sitio)
    if not h or not s:
        return False
    return h == s or h.endswith("." + s)


def _etiquetas(dominio: str) -> List[str]:
    return [p for p in normalizar(dominio).split(".") if p]


def resolver(hosts: Iterable[str], texto: str) -> Dict[str, Any]:
    """Traduce lo que escribió la persona al sitio concreto de la captura.

    Devuelve una de tres cosas:

        {"sitio": "linkedin.com", "hosts": [...]}   resolvió a uno solo
        {"candidatos": [...]}                        ambiguo: NO se elige
        {"candidatos": [], "sitios": [...]}          no matcheó nada

    La decisión de no adivinar ante ambigüedad es deliberada: elegir el de más
    tráfico parece cómodo pero puede tomar el dominio equivocado en silencio,
    que es exactamente el problema que este módulo viene a arreglar. La
    descripción de `listar_sitios_capturados` ya le pedía al modelo preguntar
    cuando hay más de uno; ahora el código lo respalda.
    """
    conocidos = [normalizar(h) for h in hosts if normalizar(h)]
    consulta = normalizar(texto)
    if not consulta:
        return {"candidatos": [], "sitios": sorted(set(map(registrable, conocidos)))}

    # Caso 1: parece un dominio (tiene punto). Se matchea exacto o subdominio.
    if "." in consulta:
        coinciden = sorted({h for h in conocidos if pertenece(h, consulta)})
        if coinciden:
            return {"sitio": consulta, "hosts": coinciden}
        return {"candidatos": [], "sitios": sorted(set(map(registrable, conocidos)))}

    # Caso 2: una palabra suelta ('linkedin'). Se compara contra las ETIQUETAS
    # del dominio registrable, no como subcadena: así 'linkedin' encuentra
    # linkedin.com pero no notlinkedin.com ni phish-linkedin.ru.
    por_dominio: Dict[str, List[str]] = {}
    for h in conocidos:
        por_dominio.setdefault(registrable(h), []).append(h)

    candidatos = sorted(d for d in por_dominio if consulta in _etiquetas(d))

    if len(candidatos) == 1:
        d = candidatos[0]
        return {"sitio": d, "hosts": sorted(set(por_dominio[d]))}
    if candidatos:
        return {"candidatos": [{"sitio": d, "hosts": sorted(set(por_dominio[d]))}
                               for d in candidatos]}
    return {"candidatos": [], "sitios": sorted(por_dominio)}


def sitios_de(hosts: Iterable[str]) -> List[str]:
    """Los dominios registrables presentes, para ofrecerlos como lista."""
    return sorted({registrable(h) for h in hosts if normalizar(h)})
