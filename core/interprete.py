"""
Which Python to use when the agent needs to run Python.

It looks trivial —`sys.executable`— and it isn't, in two situations:

  · **Own runtime.** When the launcher downloaded its own Python, there's no
    venv: looking for `venv/bin/python3` (which is what core/programador.py used
    to do) finds nothing and falls back silently.

  · **Packaged with PyInstaller.** There `sys.executable` is the app's .exe,
    NOT an interpreter. Any `subprocess.run([sys.executable, script])` relaunches
    the whole application instead of running the script. There are four places
    in the code that do exactly that (core/ejecucion.py, core/paquetes.py,
    core/biblioteca.py and the scheduler), so freezing the app without solving
    this breaks it in ways that are hard to diagnose.

This module is the only place that decides, so that the day an .exe is built
there's one thing to fix and not four.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from core import plataforma


def _frozen() -> bool:
    """Are we inside a PyInstaller bundle?"""
    return getattr(sys, "frozen", False)


def _beside_executable() -> Optional[Path]:
    """The interpreter shipped next to the .exe, if it was packaged."""
    if not _frozen():
        return None
    base = Path(sys.executable).parent
    name = "python.exe" if plataforma.ES_WINDOWS else "python3"
    for cand in (base / name, base / "runtime" / "python" / name,
                 base / "_internal" / name):
        if cand.exists():
            return cand
    return None


def _is_store_stub(path: str) -> bool:
    """The python.exe that Windows leaves on the PATH by default is NOT Python:
    it's a shortcut that opens the Microsoft Store. Running it executes nothing
    and opens a store for the user, so it can never be the answer."""
    return plataforma.ES_WINDOWS and "windowsapps" in str(path).lower()


def _works(path: str) -> bool:
    """Checked by running it, not by looking at whether the file exists — which
    is the only way to tell a real interpreter from a shortcut."""
    if not path or _is_store_stub(path):
        return False
    import subprocess
    try:
        return subprocess.run([path, "-c", "import sys"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Invented and deliberately descriptive name: if the frozen app finds no
# interpreter, subprocess fails with a FileNotFoundError that NAMES the problem,
# instead of silently running something incorrect.
NO_INTERPRETER = "python-not-found-beside-the-app"


def interpreter() -> str:
    """The Python to run the agent's scripts and modules with.

    It's the same one running the app, unless the app is frozen: there
    sys.executable isn't an interpreter and we have to look for the one shipped
    alongside."""
    if not _frozen():
        return sys.executable

    beside = _beside_executable()
    if beside:
        return str(beside)

    # With no interpreter alongside, try the system one, skipping the Store
    # shortcut. Returning sys.executable would be worse: it would relaunch the
    # whole app.
    from shutil import which
    for name in ("python3", "python"):
        found = which(name)
        if found and _works(found):
            return found
    return NO_INTERPRETER


def has_interpreter() -> bool:
    """So the UI can warn before offering something that won't work."""
    return interpreter() != NO_INTERPRETER


def interpreter_no_console() -> str:
    """Same as interpreter(), but without a console window.

    On Windows, pythonw.exe is the GUI-subsystem variant. It matters for
    scheduled tasks: with python.exe, each run pops a black window at the person
    that appears on its own and closes. Off Windows it's the same as
    interpreter()."""
    base = Path(interpreter())
    if not plataforma.ES_WINDOWS:
        return str(base)
    no_console = base.with_name("pythonw.exe")
    return str(no_console if no_console.exists() else base)


def utf8_environment(extra: Optional[dict] = None) -> dict:
    """Environment for a Python child of ours, with output in UTF-8.

    Without PYTHONIOENCODING, a script that prints '→' with stdout redirected to
    a pipe uses the local encoding (cp1252 here) and dies with
    UnicodeEncodeError. Harmless on macOS, essential on Windows."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if extra:
        env.update(extra)
    return env
