"""
Number formatting in the Spanish convention, in a single place.

Comma for decimals and dot for thousands (1.234,56), and magnitudes abbreviated
with K and M. It's centralized so the two styles don't get mixed across
different parts of the UI, which is what happens when each screen solves it on
its own with an f-string.

(The formatting itself stays in the Spanish locale on purpose: it's the number
style the app currently shows. Making it follow the UI language is a separate,
locale-aware concern for the i18n phase, not a rename.)
"""

from typing import Optional


def decimal_str(value: float, places: int = 2) -> str:
    """1234.5 -> '1.234,5' · 0.059 -> '0,06'"""
    try:
        text = f"{float(value):,.{places}f}"
    except (TypeError, ValueError):
        return str(value)
    # Python uses ',' for thousands and '.' for decimals: both are swapped.
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def compact(n: float) -> str:
    """Large numbers abbreviated: 847 · 15,7K · 13,54M · 1,20B"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)

    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}{decimal_str(n / 1_000_000_000, 2)}B"
    if n >= 1_000_000:
        return f"{sign}{decimal_str(n / 1_000_000, 2)}M"
    if n >= 1_000:
        return f"{sign}{decimal_str(n / 1_000, 1)}K"
    return f"{sign}{decimal_str(n, 0)}"


def money(value: float, prefix: str = "US$ ") -> str:
    """Precision by magnitude: cents matter when the total is small, and get in
    the way when it's tens of dollars."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return f"{prefix}—"

    if value == 0:
        return f"{prefix}0"
    if abs(value) >= 10:
        return f"{prefix}{decimal_str(value, 2)}"
    if abs(value) >= 0.01:
        return f"{prefix}{decimal_str(value, 3)}"
    return f"{prefix}{decimal_str(value, 5)}"


def duration(s: float) -> str:
    """12,4s · 3m 05s · 1h 12m"""
    try:
        s = float(s)
    except (TypeError, ValueError):
        return str(s)
    if s < 60:
        return f"{decimal_str(s, 1)}s" if s < 10 else f"{decimal_str(s, 0)}s"
    if s < 3600:
        return f"{int(s // 60)}m {int(s % 60):02d}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60):02d}m"


def size(bytes_: float) -> str:
    """File size: 847 B · 12,3 KB · 1,74 MB"""
    try:
        total = float(bytes_)
    except (TypeError, ValueError):
        return str(bytes_)
    for unit in ("B", "KB", "MB", "GB"):
        if total < 1024:
            return f"{decimal_str(total, 0 if unit == 'B' else 1)} {unit}"
        total /= 1024
    return f"{decimal_str(total, 1)} TB"


def percent(fraction: float, places: int = 1) -> str:
    return f"{decimal_str(float(fraction) * 100, places)}%"
