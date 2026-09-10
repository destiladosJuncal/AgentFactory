"""
Salida de consola en UTF-8.

Existe por un modo de falla concreto y muy tonto: en Windows, la consola usa la
codepage local (cp850/cp1252 según el caso) y `print("🏭 AgentFactory")` levanta

    UnicodeEncodeError: 'charmap' codec can't encode character '\\U0001f3ed'

El código está lleno de emoji —son parte de cómo se lee la salida— así que sin
esto los modos de consola no arrancan siquiera para mostrar su primera línea.
En macOS y Linux no cambia nada: ya vienen en UTF-8.

Se llama primero de todo en cada punto de entrada que imprima.
"""

import sys


def configurar_utf8():
    """Best-effort: si no se puede, se sigue igual. Nunca levanta."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Puede no existir (pythonw deja stdout en None) o no ser
            # reconfigurable (ya redirigido a un archivo). En ninguno de los
            # dos casos hay nada que hacer, ni nada que romper.
            pass
