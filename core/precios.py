"""
Prices per million tokens, so the UI can tell you how much you spent.

There are two sources:

  1. The table below, verified by hand against the official pages on the date
     VERIFIED_ON says.
  2. The "Update from the web" button, which re-reads those pages.

About point 2, something worth knowing: **neither provider publishes a pricing
API**. All you can do is read their documentation and parse it, and that breaks
when they change the format. That's why the update never saves on its own: it
shows you what it found, compared to what you have, and you decide. A
mis-parsed number here isn't a cosmetic bug, it's money miscalculated.

The input prices are for tokens WITHOUT cache (the normal case). If you use
prompt caching, the real cost is lower: on Claude a cache hit costs 0.1x, and on
DeepSeek the hit is ~1/120 of the miss.
"""

import json
import re
import ssl
import urllib.request
from typing import Any, Dict, Optional, Tuple

VERIFIED_ON = "2026-08-13"

SOURCES = {
    "claude": "https://platform.claude.com/docs/en/about-claude/pricing.md",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing",
}

# (input, output) in dollars per million tokens.
KNOWN_PRICES: Dict[str, Tuple[float, float]] = {
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
    # --- DeepSeek (input = cache miss) ---
    "deepseek-v4-pro":    (0.435, 0.87),
    "deepseek-v4-flash":  (0.14, 0.28),
}

# Dated notices that affect the calculation and aren't visible in the table.
NOTES = [
    ("2026-08-16", "deepseek",
     "From 2026-08-16 DeepSeek switches to time-of-day billing: off-peak it "
     "charges HALF. This app's calculation uses the full rate, so from that "
     "date on it will overestimate nighttime spend."),
]


def as_env(prices: Optional[Dict[str, Tuple[float, float]]] = None) -> Dict[str, str]:
    """Converts the table to the key format core/proveedores.py reads."""
    prices = prices if prices is not None else KNOWN_PRICES
    out: Dict[str, str] = {}
    for model, (input_, output) in prices.items():
        key = re.sub(r"[^A-Z0-9]+", "_", model.upper()).strip("_")
        out[f"PRECIO_{key}_IN"] = str(input_)
        out[f"PRECIO_{key}_OUT"] = str(output)
    return out


# --- Web lookup -------------------------------------------------------------

def _download(url: str, timeout: int = 20) -> str:
    context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": "AgenteDeepSeek/1.0"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as r:
        return r.read().decode("utf-8", errors="replace")


def _to_number(text: str) -> Optional[float]:
    m = re.search(r"\$?\s*([\d.]+)", text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_claude(page: str) -> Dict[str, Tuple[float, float]]:
    """Reads the model table from Anthropic's docs.

    Expected format (markdown):
        | Claude Opus 4.8 | $5 / MTok | ... | $25 / MTok |
    The first price column is the base input and the last one is the output.
    """
    found: Dict[str, Tuple[float, float]] = {}
    for line in page.splitlines():
        if not line.strip().startswith("|") or "MTok" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = re.sub(r"\[.*?\]\(.*?\)", "", cells[0]).strip()
        m = re.match(r"Claude\s+([\w.\s]+)", name)
        if not m:
            continue

        prices = [v for v in (_to_number(c) for c in cells[1:] if "MTok" in c)
                  if v is not None]
        # The page has TWO tables with the same row format: the one for normal
        # prices (5 columns: input, 3 cache, output) and the Batch one (2
        # columns, 50% less). Without this filter the last one read wins and
        # all prices come out halved.
        if len(prices) < 4:
            continue

        model = "claude-" + re.sub(r"[.\s]+", "-", m.group(1).strip().lower())
        found[model] = (prices[0], prices[-1])
    return found


def _parse_deepseek(page: str) -> Dict[str, Tuple[float, float]]:
    """Reads DeepSeek's table. Its docs are HTML, so it's cleaned up first.

    The input price taken is WITHOUT cache (cache miss), which is the normal and
    conservative case: if you have cache hits, you'll spend less than the app
    shows, never more.
    """
    text = re.sub(r"<[^>]+>", "|", page)
    text = re.sub(r"\|+", "|", text)

    # WATCH the format: DeepSeek puts the MODELS AS COLUMNS, not as rows. That
    # is, a row is "1M INPUT TOKENS (CACHE MISS) | $0.14 | $0.435", where the
    # first price is for the first model in the header and the second for the
    # second. Parsing this by rows, like the Anthropic table, returns the
    # numbers shifted by a column.
    order = re.search(r"MODEL\|((?:deepseek-[\w.-]+\|?)+)", text, re.IGNORECASE)
    if not order:
        return {}
    models = [m for m in order.group(1).split("|") if m.strip()]

    def row(label: str):
        m = re.search(re.escape(label) + r"[^|]*\|((?:\s*\$[\d.]+\s*\|?)+)", text, re.I)
        if not m:
            return None
        return [float(v) for v in re.findall(r"\$\s*([\d.]+)", m.group(1))]

    inputs = row("CACHE MISS)")
    outputs = row("1M OUTPUT TOKENS")
    if not inputs or not outputs:
        return {}

    found: Dict[str, Tuple[float, float]] = {}
    for i, model in enumerate(models):
        if i < len(inputs) and i < len(outputs):
            found[model.strip()] = (inputs[i], outputs[i])
    return found


def fetch_from_web() -> Dict[str, Any]:
    """Re-reads the official pages. Saves NOTHING: returns what it found so the
    UI can show it and the person confirms."""
    result: Dict[str, Any] = {"encontrados": {}, "errores": [], "fuentes": SOURCES}

    for name, url in SOURCES.items():
        try:
            page = _download(url)
        except Exception as e:
            result["errores"].append(f"{name}: couldn't read {url} ({e})")
            continue
        try:
            parsed = _parse_claude(page) if name == "claude" else _parse_deepseek(page)
        except Exception as e:
            result["errores"].append(f"{name}: the page changed format ({e})")
            continue
        if not parsed:
            result["errores"].append(
                f"{name}: read the page but recognized no pricing table. "
                f"They probably changed the format — check {url} by hand.")
            continue
        result["encontrados"].update(parsed)

    return result


def compare(new: Dict[str, Tuple[float, float]],
             current: Dict[str, Tuple[float, float]]) -> Dict[str, Any]:
    """What would change if the new prices were applied."""
    changes, same, added = [], [], []
    for model, (input_, output) in sorted(new.items()):
        old = current.get(model)
        if old is None:
            added.append((model, input_, output))
        elif abs(old[0] - input_) > 1e-9 or abs(old[1] - output) > 1e-9:
            changes.append((model, old, (input_, output)))
        else:
            same.append(model)
    return {"cambios": changes, "iguales": same, "agregados": added}
