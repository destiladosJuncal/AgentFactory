"""
Domain comparison: a single place that decides whether two hosts are the same site.

It exists because of a concrete bug. The tool the agent uses to search the
capture filtered with `host LIKE '%sitio%'`, i.e. by substring. That did two
things wrong at once:

    site='linkedin.com'  ->  also brought  notlinkedin.com
    site='google.com'    ->  also brought  google.com.ar.phish.net
    site='linkedin'      ->  brought 6 hosts, among them phish-linkedin.ru

The first harm is correctness: data from other domains mixed silently into the
extraction. The second is security: content from a similar domain —or outright a
lookalike— reaches the model presented as if it were from the real site.

The striking part is that the app already knew how to do it right elsewhere: the
capture filter (`core/proxy.py:_interesa`) compares with `host == h or
host.endswith("." + h)`, which is correct. This module generalizes that rule and
puts it where everyone can use it.

To know which part of a host is the domain someone registered we use the Public
Suffix List, via publicsuffix2 (already shipped with mitmproxy). Without it you
can't tell 'google.com.ar' (registrable) from 'phish.net' in
'google.com.ar.phish.net': you need the real list of public suffixes, it can't
be deduced by counting dots.
"""

from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit


def _without_scheme(text: str) -> str:
    """Accepts being handed a whole URL and not just the host.

    The model sends 'https://linkedin.com/jobs' as naturally as 'linkedin.com',
    and rejecting it would be needlessly fussy."""
    text = (text or "").strip().strip("<>\"'")
    if "//" in text:
        text = urlsplit(text if "://" in text else "http://" + text).netloc
    text = text.split("/")[0]
    if "@" in text:                       # user:pass@host
        text = text.rsplit("@", 1)[1]
    if text.startswith("["):              # IPv6 in brackets
        return text.split("]")[0] + "]"
    return text.split(":")[0]             # drop the port


def normalize(host: str) -> str:
    """Host in lowercase, no trailing dot, no scheme or port."""
    return _without_scheme(host).lower().rstrip(".").lstrip(".")


def registrable(host: str) -> str:
    """The domain someone registered: 'accounts.google.com' -> 'google.com'.

    Returns the normalized host when it can't be determined (an IP, a
    single-label host, or if the library isn't there)."""
    h = normalize(host)
    if not h or h.replace(".", "").isdigit() or h.startswith("["):
        return h                          # IP: has no registrable domain
    try:
        from publicsuffix2 import get_sld
        sld = get_sld(h)
        return (sld or h).lower()
    except Exception:
        # Without publicsuffix2 it degrades to the last two labels. It's worse
        # (doesn't tell 'com.ar' apart) but still far better than substring.
        parts = h.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else h


def belongs_to(host: str, site: str) -> bool:
    """Is `host` `site` or a subdomain of it?

    It's the rule of core/proxy.py:_interesa: exact equality or dotted suffix.
    'notlinkedin.com' does NOT belong to 'linkedin.com', which is exactly what
    the substring comparison failed to tell apart."""
    h, s = normalize(host), normalize(site)
    if not h or not s:
        return False
    return h == s or h.endswith("." + s)


def _labels(domain: str) -> List[str]:
    return [p for p in normalize(domain).split(".") if p]


def resolve(hosts: Iterable[str], text: str) -> Dict[str, Any]:
    """Translates what the person typed into the concrete site in the capture.

    Returns one of three things:

        {"sitio": "linkedin.com", "hosts": [...]}   resolved to a single one
        {"candidatos": [...]}                        ambiguous: NOT chosen
        {"candidatos": [], "sitios": [...]}          matched nothing

    The decision not to guess on ambiguity is deliberate: picking the one with
    the most traffic seems convenient but can take the wrong domain silently,
    which is exactly the problem this module comes to fix. The description of
    `listar_sitios_capturados` already asked the model to ask when there's more
    than one; now the code backs it up.
    """
    known = [normalize(h) for h in hosts if normalize(h)]
    query = normalize(text)
    if not query:
        return {"candidatos": [], "sitios": sorted(set(map(registrable, known)))}

    # Case 1: it looks like a domain (has a dot). Matched exact or subdomain.
    if "." in query:
        matches = sorted({h for h in known if belongs_to(h, query)})
        if matches:
            return {"sitio": query, "hosts": matches}
        return {"candidatos": [], "sitios": sorted(set(map(registrable, known)))}

    # Case 2: a bare word ('linkedin'). Compared against the LABELS of the
    # registrable domain, not as a substring: so 'linkedin' finds linkedin.com
    # but not notlinkedin.com nor phish-linkedin.ru.
    by_domain: Dict[str, List[str]] = {}
    for h in known:
        by_domain.setdefault(registrable(h), []).append(h)

    candidates = sorted(d for d in by_domain if query in _labels(d))

    if len(candidates) == 1:
        d = candidates[0]
        return {"sitio": d, "hosts": sorted(set(by_domain[d]))}
    if candidates:
        return {"candidatos": [{"sitio": d, "hosts": sorted(set(by_domain[d]))}
                               for d in candidates]}
    return {"candidatos": [], "sitios": sorted(by_domain)}


def sites_of(hosts: Iterable[str]) -> List[str]:
    """The registrable domains present, to offer them as a list."""
    return sorted({registrable(h) for h in hosts if normalize(h)})
