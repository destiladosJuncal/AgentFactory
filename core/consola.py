"""
Console output in UTF-8.

It exists because of one concrete and very silly failure mode: on Windows, the
console uses the local codepage (cp850/cp1252 depending on the case) and
`print("🏭 AgentFactory")` raises

    UnicodeEncodeError: 'charmap' codec can't encode character '\\U0001f3ed'

The code is full of emoji —they're part of how the output reads— so without this
the console modes don't even start far enough to show their first line. On macOS
and Linux nothing changes: they're already UTF-8.

It's called first thing in every entry point that prints.
"""

import sys


def setup_utf8():
    """Best-effort: if it can't, it carries on. Never raises."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # It may not exist (pythonw leaves stdout as None) or not be
            # reconfigurable (already redirected to a file). In neither case is
            # there anything to do, nor anything to break.
            pass
