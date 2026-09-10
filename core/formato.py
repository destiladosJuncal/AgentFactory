"""
Formato de números en convención castellana, en un solo lugar.

Coma para decimales y punto para los miles (1.234,56), y magnitudes abreviadas
con K y M. Está centralizado para que no queden mezclados los dos estilos en
distintas partes de la interfaz, que es lo que pasa cuando cada pantalla lo
resuelve por su cuenta con un f-string.
"""

from typing import Optional


def dec(valor: float, decimales: int = 2) -> str:
    """1234.5 -> '1.234,5' · 0.059 -> '0,06'"""
    try:
        texto = f"{float(valor):,.{decimales}f}"
    except (TypeError, ValueError):
        return str(valor)
    # Python usa ',' para miles y '.' para decimales: se invierten los dos.
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def cantidad(n: float) -> str:
    """Números grandes abreviados: 847 · 15,7K · 13,54M · 1,20B"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)

    signo = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{signo}{dec(n / 1_000_000_000, 2)}B"
    if n >= 1_000_000:
        return f"{signo}{dec(n / 1_000_000, 2)}M"
    if n >= 1_000:
        return f"{signo}{dec(n / 1_000, 1)}K"
    return f"{signo}{dec(n, 0)}"


def dinero(valor: float, prefijo: str = "US$ ") -> str:
    """Precisión según la magnitud: los centavos importan cuando el total es
    chico, y estorban cuando son decenas de dólares."""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return f"{prefijo}—"

    if valor == 0:
        return f"{prefijo}0"
    if abs(valor) >= 10:
        return f"{prefijo}{dec(valor, 2)}"
    if abs(valor) >= 0.01:
        return f"{prefijo}{dec(valor, 3)}"
    return f"{prefijo}{dec(valor, 5)}"


def segundos(s: float) -> str:
    """12,4s · 3m 05s · 1h 12m"""
    try:
        s = float(s)
    except (TypeError, ValueError):
        return str(s)
    if s < 60:
        return f"{dec(s, 1)}s" if s < 10 else f"{dec(s, 0)}s"
    if s < 3600:
        return f"{int(s // 60)}m {int(s % 60):02d}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60):02d}m"


def tamano(bytes_: float) -> str:
    """Tamaño de archivo: 847 B · 12,3 KB · 1,74 MB"""
    try:
        total = float(bytes_)
    except (TypeError, ValueError):
        return str(bytes_)
    for unidad in ("B", "KB", "MB", "GB"):
        if total < 1024:
            return f"{dec(total, 0 if unidad == 'B' else 1)} {unidad}"
        total /= 1024
    return f"{dec(total, 1)} TB"


def porcentaje(fraccion: float, decimales: int = 1) -> str:
    return f"{dec(float(fraccion) * 100, decimales)}%"
