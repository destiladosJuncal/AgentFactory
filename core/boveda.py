"""
Bóveda de secretos para las ACCIONES del agente (no son las API keys de los
proveedores).

Idea: el agente nunca conoce el valor. Pide una credencial por NOMBRE (etiqueta)
-> se le abre una ventana a la persona para que la ingrese -> se guarda CIFRADA.
El agente solo puede USARLA poniendo `{{secreto:ETIQUETA}}` en un comando; recién
en el borde del subprocess (y con tu aprobación) AgentFactory sustituye el valor
real. No hay ninguna tool que devuelva el valor: el modelo nunca lo ve, ni en el
prompt, ni en el historial, ni en la salida (el redactor lo tacha).

Límite honesto: un agente que corre código local arbitrario como tu usuario
podría, en teoría, leer archivos locales. Esta bóveda garantiza que el VALOR no
llega al LLM y que cada USO pasa por tu aprobación; no es un sandbox del proceso.
"""

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core import secretos

# {{secreto:ETIQUETA}} — etiqueta alfanumérica con _ y -.
PLACEHOLDER = re.compile(r"\{\{\s*secreto\s*:\s*([A-Za-z0-9_\-]{1,64})\s*\}\}")
_ETIQUETA_OK = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# La UI registra acá cómo pedirle el valor a la persona (ventana modal). Devuelve
# el valor ingresado, o None si se canceló. El agente NUNCA ve ese valor.
SOLICITADOR: Optional[Callable[[str, str], Optional[str]]] = None


def registrar_solicitador(fn: Optional[Callable[[str, str], Optional[str]]]):
    global SOLICITADOR
    SOLICITADOR = fn


def _ruta() -> Path:
    from core.rutas import dir_datos
    return dir_datos() / "_boveda.json"


def _leer() -> Dict[str, str]:
    p = _ruta()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _escribir(d: Dict[str, str]):
    p = _ruta()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    secretos._protect(p) if hasattr(secretos, "_protect") else None


def etiqueta_valida(etiqueta: str) -> bool:
    return bool(_ETIQUETA_OK.match((etiqueta or "").strip()))


def guardar(etiqueta: str, valor: str) -> Dict[str, Any]:
    """Guarda (cifrado) el valor bajo la etiqueta. Pisa si ya existía."""
    etiqueta = (etiqueta or "").strip()
    if not etiqueta_valida(etiqueta):
        return {"ok": False, "error": "etiqueta inválida (usá letras, números, _ y -)"}
    if valor is None or valor == "":
        return {"ok": False, "error": "valor vacío"}
    d = _leer()
    d[etiqueta] = secretos.encrypt(valor)
    _escribir(d)
    return {"ok": True, "etiqueta": etiqueta, "cifrado": secretos.is_encrypted(d[etiqueta])}


def etiquetas() -> List[str]:
    """Solo los NOMBRES. Nunca los valores."""
    return sorted(_leer().keys())


def tiene(etiqueta: str) -> bool:
    return (etiqueta or "").strip() in _leer()


def borrar(etiqueta: str) -> Dict[str, Any]:
    d = _leer()
    if (etiqueta or "").strip() not in d:
        return {"ok": False, "error": "no existe"}
    del d[etiqueta.strip()]
    _escribir(d)
    return {"ok": True}


def _valor(etiqueta: str) -> Optional[str]:
    """INTERNO: descifra el valor. Solo se usa en el borde del subprocess y por
    el redactor. No se expone al agente ni a la UI."""
    d = _leer()
    cif = d.get((etiqueta or "").strip())
    if cif is None:
        return None
    return secretos.decrypt(cif)


def valores() -> Set[str]:
    """INTERNO (redactor): todos los valores en claro, para tacharlos de la salida."""
    out = set()
    for et in _leer():
        v = _valor(et)
        if v:
            out.add(v)
    return out


def etiquetas_en(texto: str) -> List[str]:
    """Qué etiquetas `{{secreto:...}}` aparecen en el texto (sin tocar valores).
    Sirve para mostrarte qué secretos usaría un comando antes de aprobarlo."""
    if not texto:
        return []
    vistas = []
    for m in PLACEHOLDER.finditer(texto):
        if m.group(1) not in vistas:
            vistas.append(m.group(1))
    return vistas


def sustituir(texto: str) -> Tuple[str, List[str], List[str]]:
    """INTERNO: reemplaza {{secreto:ETIQUETA}} por el valor real. Devuelve
    (texto_sustituido, etiquetas_usadas, etiquetas_faltantes)."""
    usadas, faltan = [], []

    def _rep(m):
        et = m.group(1)
        v = _valor(et)
        if v is None:
            if et not in faltan:
                faltan.append(et)
            return m.group(0)  # se deja el placeholder; el caller decide
        if et not in usadas:
            usadas.append(et)
        return v

    return PLACEHOLDER.sub(_rep, texto or ""), usadas, faltan


# ---- interacción con el agente (pedir / listar) ----------------------------

def pedir(etiqueta: str, motivo: str = "") -> Dict[str, Any]:
    """El agente pide una credencial. Si ya existe, no re-pregunta. Si no, abre
    la ventana para que la persona la ingrese. NUNCA devuelve el valor."""
    etiqueta = (etiqueta or "").strip()
    if not etiqueta_valida(etiqueta):
        return {"ok": False, "error": "etiqueta inválida"}
    if tiene(etiqueta):
        return {"ok": True, "etiqueta": etiqueta, "ya_existe": True,
                "uso": f"usala poniendo {{{{secreto:{etiqueta}}}}} en un comando"}
    if SOLICITADOR is None:
        return {"ok": False, "error": "no hay forma de pedir el secreto en este modo"}
    try:
        valor = SOLICITADOR(etiqueta, motivo or "")
    except Exception as e:
        return {"ok": False, "error": f"no se pudo pedir el secreto: {e}"}
    if not valor:
        return {"ok": False, "error": "la persona canceló el ingreso del secreto"}
    guardar(etiqueta, valor)
    return {"ok": True, "etiqueta": etiqueta, "ya_existe": False,
            "uso": f"usala poniendo {{{{secreto:{etiqueta}}}}} en un comando"}


def ejecutar_tool_secreto(nombre: str, argumentos: dict) -> Dict[str, Any]:
    a = argumentos or {}
    if nombre == "pedir_secreto":
        return pedir(a.get("etiqueta", ""), a.get("motivo", ""))
    if nombre == "listar_secretos":
        return {"etiquetas": etiquetas()}
    return {"error": f"herramienta de secretos desconocida: {nombre}"}


TOOLS_SCHEMA_SECRETOS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "pedir_secreto",
            "description": (
                "Pedí una CREDENCIAL/token que necesitás y NO tenés (API key, "
                "token, contraseña). Se le abre una ventana a la persona para que "
                "la ingrese y se guarda CIFRADA bajo la etiqueta. IMPORTANTE: vos "
                "NUNCA vas a ver el valor. Para usarla, poné el placeholder "
                "{{secreto:ETIQUETA}} dentro de un comando de shell/python; "
                "AgentFactory lo reemplaza por el valor real al ejecutar (con "
                "confirmación de la persona). No intentes leer ni imprimir el valor."),
            "parameters": {
                "type": "object",
                "properties": {
                    "etiqueta": {"type": "string",
                                 "description": "Nombre del secreto, ej: GITHUB_TOKEN (letras/números/_/-)"},
                    "motivo": {"type": "string",
                               "description": "Para qué lo necesitás (se le muestra a la persona)"},
                },
                "required": ["etiqueta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_secretos",
            "description": ("Lista las ETIQUETAS de los secretos ya guardados (solo "
                            "los nombres, nunca los valores). Usalos con "
                            "{{secreto:ETIQUETA}} en tus comandos."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

NOMBRES_TOOLS_SECRETOS = {t["function"]["name"] for t in TOOLS_SCHEMA_SECRETOS}
