"""
Precios por millón de tokens, para que la UI pueda decirte cuánto gastaste.

Hay dos fuentes:

  1. La tabla de abajo, verificada a mano contra las páginas oficiales en la
     fecha que dice FECHA_VERIFICACION.
  2. El botón "Actualizar desde la web", que vuelve a leer esas páginas.

Sobre el punto 2, algo que conviene saber: **ninguno de los dos proveedores
publica una API de precios**. Lo único que se puede hacer es leer su
documentación y parsearla, y eso se rompe cuando cambian el formato. Por eso
la actualización nunca guarda sola: te muestra qué encontró, comparado con lo
que tenés, y vos decidís. Un número mal parseado acá no es un bug cosmético,
es plata mal calculada.

Los precios de entrada son los de tokens SIN caché (el caso normal). Si usás
prompt caching, lo real es más barato: en Claude un cache hit sale 0.1x, y en
DeepSeek el hit es ~1/120 del miss.
"""

import json
import re
import ssl
import urllib.request
from typing import Any, Dict, Optional, Tuple

FECHA_VERIFICACION = "2026-08-13"

FUENTES = {
    "claude": "https://platform.claude.com/docs/en/about-claude/pricing.md",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing",
}

# (entrada, salida) en dólares por millón de tokens.
PRECIOS_CONOCIDOS: Dict[str, Tuple[float, float]] = {
    # --- Anthropic ---
    "claude-opus-5":      (5.0, 25.0),
    "claude-opus-4-8":    (5.0, 25.0),
    "claude-opus-4-7":    (5.0, 25.0),
    "claude-opus-4-6":    (5.0, 25.0),
    "claude-opus-4-5":    (5.0, 25.0),
    "claude-fable-5":     (10.0, 50.0),
    "claude-sonnet-5":    (2.0, 10.0),
    "claude-sonnet-4-6":  (3.0, 15.0),
    "claude-sonnet-4-5":  (3.0, 15.0),
    "claude-haiku-4-5":   (1.0, 5.0),
    # --- DeepSeek (entrada = cache miss) ---
    "deepseek-v4-pro":    (0.435, 0.87),
    "deepseek-v4-flash":  (0.14, 0.28),
}

# Avisos con fecha que afectan al cálculo y no se ven en la tabla.
NOTAS = [
    ("2026-08-16", "deepseek",
     "Desde el 16/08/2026 DeepSeek pasa a facturación por franja horaria: "
     "fuera de pico cobra la MITAD. El cálculo de esta app usa tarifa plena, "
     "así que a partir de esa fecha va a sobreestimar el gasto nocturno."),
]


def como_env(precios: Optional[Dict[str, Tuple[float, float]]] = None) -> Dict[str, str]:
    """Convierte la tabla al formato de claves que lee core/proveedores.py."""
    precios = precios if precios is not None else PRECIOS_CONOCIDOS
    salida: Dict[str, str] = {}
    for modelo, (entrada, sal) in precios.items():
        clave = re.sub(r"[^A-Z0-9]+", "_", modelo.upper()).strip("_")
        salida[f"PRECIO_{clave}_IN"] = str(entrada)
        salida[f"PRECIO_{clave}_OUT"] = str(sal)
    return salida


# --- Consulta a la web ------------------------------------------------------

def _bajar(url: str, timeout: int = 20) -> str:
    contexto = ssl.create_default_context()
    pedido = urllib.request.Request(url, headers={"User-Agent": "AgenteDeepSeek/1.0"})
    with urllib.request.urlopen(pedido, timeout=timeout, context=contexto) as r:
        return r.read().decode("utf-8", errors="replace")


def _a_numero(texto: str) -> Optional[float]:
    m = re.search(r"\$?\s*([\d.]+)", texto.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parsear_claude(pagina: str) -> Dict[str, Tuple[float, float]]:
    """Lee la tabla de modelos de la doc de Anthropic.

    Formato esperado (markdown):
        | Claude Opus 4.8 | $5 / MTok | ... | $25 / MTok |
    La primera columna de precio es la entrada base y la última la salida.
    """
    encontrados: Dict[str, Tuple[float, float]] = {}
    for linea in pagina.splitlines():
        if not linea.strip().startswith("|") or "MTok" not in linea:
            continue
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        nombre = re.sub(r"\[.*?\]\(.*?\)", "", celdas[0]).strip()
        m = re.match(r"Claude\s+([\w.\s]+)", nombre)
        if not m:
            continue

        precios = [v for v in (_a_numero(c) for c in celdas[1:] if "MTok" in c)
                   if v is not None]
        # La página trae DOS tablas con el mismo formato de filas: la de
        # precios normales (5 columnas: entrada, 3 de caché, salida) y la de
        # Batch (2 columnas, 50% menos). Sin este filtro gana la última leída
        # y todos los precios salen a la mitad.
        if len(precios) < 4:
            continue

        modelo = "claude-" + re.sub(r"[.\s]+", "-", m.group(1).strip().lower())
        encontrados[modelo] = (precios[0], precios[-1])
    return encontrados


def _parsear_deepseek(pagina: str) -> Dict[str, Tuple[float, float]]:
    """Lee la tabla de DeepSeek. Su doc es HTML, así que se limpia primero.

    Se toma el precio de entrada SIN caché (cache miss), que es el caso normal
    y el conservador: si tenés aciertos de caché, vas a gastar menos de lo que
    muestre la app, nunca más.
    """
    texto = re.sub(r"<[^>]+>", "|", pagina)
    texto = re.sub(r"\|+", "|", texto)

    # OJO con el formato: DeepSeek pone los MODELOS COMO COLUMNAS, no como
    # filas. O sea que una fila es "1M INPUT TOKENS (CACHE MISS) | $0.14 |
    # $0.435", donde el primer precio es del primer modelo del encabezado y el
    # segundo del segundo. Parsear esto por filas, como la tabla de Anthropic,
    # devuelve los números corridos de columna.
    orden = re.search(r"MODEL\|((?:deepseek-[\w.-]+\|?)+)", texto, re.IGNORECASE)
    if not orden:
        return {}
    modelos = [m for m in orden.group(1).split("|") if m.strip()]

    def fila(etiqueta: str):
        m = re.search(re.escape(etiqueta) + r"[^|]*\|((?:\s*\$[\d.]+\s*\|?)+)", texto, re.I)
        if not m:
            return None
        return [float(v) for v in re.findall(r"\$\s*([\d.]+)", m.group(1))]

    entradas = fila("CACHE MISS)")
    salidas = fila("1M OUTPUT TOKENS")
    if not entradas or not salidas:
        return {}

    encontrados: Dict[str, Tuple[float, float]] = {}
    for i, modelo in enumerate(modelos):
        if i < len(entradas) and i < len(salidas):
            encontrados[modelo.strip()] = (entradas[i], salidas[i])
    return encontrados


def consultar_web() -> Dict[str, Any]:
    """Relee las páginas oficiales. NO guarda nada: devuelve lo encontrado
    para que la UI lo muestre y la persona confirme."""
    resultado: Dict[str, Any] = {"encontrados": {}, "errores": [], "fuentes": FUENTES}

    for nombre, url in FUENTES.items():
        try:
            pagina = _bajar(url)
        except Exception as e:
            resultado["errores"].append(f"{nombre}: no pude leer {url} ({e})")
            continue
        try:
            parseados = _parsear_claude(pagina) if nombre == "claude" else _parsear_deepseek(pagina)
        except Exception as e:
            resultado["errores"].append(f"{nombre}: la página cambió de formato ({e})")
            continue
        if not parseados:
            resultado["errores"].append(
                f"{nombre}: leí la página pero no reconocí ninguna tabla de precios. "
                f"Probablemente cambiaron el formato — revisá {url} a mano.")
            continue
        resultado["encontrados"].update(parseados)

    return resultado


def comparar(nuevos: Dict[str, Tuple[float, float]],
             actuales: Dict[str, Tuple[float, float]]) -> Dict[str, Any]:
    """Qué cambiaría si se aplicaran los precios nuevos."""
    cambios, iguales, agregados = [], [], []
    for modelo, (entrada, salida) in sorted(nuevos.items()):
        viejo = actuales.get(modelo)
        if viejo is None:
            agregados.append((modelo, entrada, salida))
        elif abs(viejo[0] - entrada) > 1e-9 or abs(viejo[1] - salida) > 1e-9:
            cambios.append((modelo, viejo, (entrada, salida)))
        else:
            iguales.append(modelo)
    return {"cambios": cambios, "iguales": iguales, "agregados": agregados}
