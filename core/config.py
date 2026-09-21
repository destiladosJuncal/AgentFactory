"""
Lectura y escritura del .env desde la UI, para no tener que editarlo a mano.

Dos cuidados que justifican que esto sea un módulo y no cuatro líneas sueltas:

  · Al guardar se PRESERVAN los comentarios y las claves que no conocemos. Un
    reescribir ingenuo del archivo te borra las notas y cualquier variable que
    hayas agregado por tu cuenta.
  · Las claves nunca se devuelven completas para mostrar: hay una función de
    enmascarado, y el valor real solo sale cuando se lo pide explícitamente
    para guardarlo o para probar la conexión.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.rutas import PLANTILLA_ENV, proteger, ruta_env

# Claves que la UI sabe editar. (clave, etiqueta, es_secreto)
CAMPOS: List[Tuple[str, str, bool]] = [
    ("AGENTE_PROVEEDOR", "Proveedor por defecto", False),
    ("DEEPSEEK_API_KEY", "DeepSeek · API key", True),
    ("DEEPSEEK_MODEL", "DeepSeek · modelo", False),
    ("DEEPSEEK_API_URL", "DeepSeek · URL", False),
    ("ANTHROPIC_API_KEY", "Claude · API key", True),
    ("ANTHROPIC_MODEL", "Claude · modelo", False),
    ("QWEN_API_KEY", "Qwen · API key", True),
    ("QWEN_MODEL", "Qwen · modelo", False),
    ("QWEN_API_URL", "Qwen · URL", False),
    ("GEMINI_API_KEY", "Gemini · API key", True),
    ("GEMINI_MODEL", "Gemini · modelo", False),
    ("GEMINI_API_URL", "Gemini · URL", False),
    ("ANTHROPIC_EFFORT", "Claude · effort", False),
]

CLAVES_SECRETAS = {c for c, _, secreto in CAMPOS if secreto}


def enmascarar(valor: str) -> str:
    """'sk-abc...xyz1234' -> 'sk-a••••••1234'. Nunca muestra el medio."""
    if not valor:
        return ""
    if len(valor) <= 10:
        return "•" * len(valor)
    return f"{valor[:4]}{'•' * 6}{valor[-4:]}"


def leer(ruta: Optional[Path] = None) -> Dict[str, str]:
    """Todas las variables del .env. Devuelve los valores REALES."""
    ruta = ruta or ruta_env()
    valores: Dict[str, str] = {}
    if not ruta.exists():
        return valores
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def guardar(cambios: Dict[str, str], ruta: Optional[Path] = None) -> Dict[str, Any]:
    """Aplica `cambios` conservando comentarios, orden y claves desconocidas."""
    ruta = ruta or ruta_env()
    if not ruta.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(PLANTILLA_ENV, encoding="utf-8")

    lineas = ruta.read_text(encoding="utf-8").splitlines()
    pendientes = dict(cambios)
    salida: List[str] = []

    for linea in lineas:
        desnuda = linea.strip()
        if desnuda and not desnuda.startswith("#") and "=" in desnuda:
            clave = desnuda.split("=", 1)[0].strip()
            if clave in pendientes:
                salida.append(f"{clave}={pendientes.pop(clave)}")
                continue
        salida.append(linea)

    # Las claves nuevas van al final, no se pierden.
    if pendientes:
        salida.append("")
        for clave, valor in pendientes.items():
            salida.append(f"{clave}={valor}")

    try:
        ruta.write_text("\n".join(salida) + "\n", encoding="utf-8")
        proteger(ruta)
    except OSError as e:
        return {"error": f"No pude guardar {ruta}: {e}"}

    # Que el cambio valga para este proceso sin reiniciar la app.
    for clave, valor in cambios.items():
        os.environ[clave] = valor

    return {"guardado": str(ruta), "claves": sorted(cambios)}


def probar(proveedor: str) -> Dict[str, Any]:
    """Hace UNA llamada mínima real y reporta qué pasó.

    Existe por lo que costó descubrir que 'DeepSeek-V4-Pro' daba 400 y
    'deepseek-v4-pro' andaba: construir el cliente no prueba nada, hay que
    hablar con la API.
    """
    from core.proveedores import ErrorProveedor, crear_proveedor

    try:
        p = crear_proveedor(proveedor)
    except Exception as e:
        return {"ok": False, "detalle": f"No pude crear el cliente: {e}"}

    if p is None:
        return {"ok": False, "detalle": "Faltan credenciales para este proveedor"}

    try:
        r = p.completar(mensajes=[{"role": "user", "content": "Respondé solo: OK"}])
    except ErrorProveedor as e:
        mensaje = str(e)
        pista = ""
        if mensaje.startswith("401") or "authentication" in mensaje.lower():
            pista = "La API key es inválida o está vencida."
        elif mensaje.startswith("400") and "model" in mensaje.lower():
            pista = "El nombre del modelo no es válido para esta API."
        elif "Connection" in mensaje or "connect" in mensaje.lower():
            pista = "No hay conexión con el servidor. ¿Internet? ¿URL correcta?"
        return {"ok": False, "detalle": mensaje[:400], "pista": pista,
                "modelo": getattr(p, "modelo", "")}
    except Exception as e:
        return {"ok": False, "detalle": f"{type(e).__name__}: {e}"[:400]}

    return {
        "ok": True,
        "modelo": getattr(p, "modelo", ""),
        "respuesta": (r.texto or "").strip()[:60],
        "tokens": r.uso.total if getattr(r, "uso", None) else 0,
    }


def asegurar_precios() -> Optional[str]:
    """Carga los precios conocidos si el .env todavía no tiene ninguno.

    Así el costo aparece desde el primer arranque en vez de mostrar '—'. No
    pisa nada: si ya hay precios cargados (tuyos o actualizados desde la web),
    los deja como están."""
    from core import precios as tabla
    actuales = leer()
    if any(c.startswith("PRECIO_") and v.strip() for c, v in actuales.items()):
        return None
    guardar(tabla.as_env())
    return (f"Cargué los precios conocidos al {tabla.VERIFIED_ON} "
            f"({len(tabla.KNOWN_PRICES)} modelos).")


def precios_actuales() -> Dict[str, tuple]:
    """Lo que hay cargado hoy en el .env, en forma de tabla."""
    from core.proveedores import precio_de
    from core import precios as tabla
    vistos = {}
    for clave in leer():
        m = re.match(r"PRECIO_(.+)_IN$", clave)
        if not m:
            continue
        # De 'CLAUDE_OPUS_4_8' se vuelve al id buscando cuál coincide.
        for modelo in list(tabla.KNOWN_PRICES) + [modelo_de_clave(m.group(1))]:
            if re.sub(r"[^A-Z0-9]+", "_", modelo.upper()).strip("_") == m.group(1):
                precio = precio_de(modelo)
                if precio:
                    vistos[modelo] = precio
                break
    return vistos


def modelo_de_clave(clave: str) -> str:
    return clave.lower().replace("_", "-")


def faltan_credenciales() -> bool:
    """True si no hay ninguna clave cargada: la UI abre en Configuración."""
    valores = leer()
    return not any(valores.get(c, "").strip() for c in CLAVES_SECRETAS)
