# AgentFactory on Windows — status

The Windows port is **done and tested** on Windows 10 Pro 19045 (es-ES), on a
machine **with no Python installed**. This document replaces the original
handoff, which described the pending work.

## How to install it on another Windows

**Recommended path — portable folder (1.1 MB):**

1. Unzip `AgentFactory-windows.zip` in full into a user folder.
2. Double-click `INICIAR.bat`.

The first time it downloads a Python 3.12.13 (~30 MB) from
`python-build-standalone`, drops it in `runtime/` inside the same folder,
installs `requirements.txt` and opens the app. It installs nothing on the system,
asks for no administrator and doesn't touch the registry. Subsequent launches are
direct and console-free.

**Alternative path — `.exe` (239 MB, `dist/AgentFactory/`):** see below.

## What was tested, and with what result

| | |
|---|---|
| Startup on a machine without Python | OK — detects and discards the Microsoft Store stub, downloads the runtime, installs and opens |
| Install from the zip into a clean folder | OK — tested by unzipping and starting as the recipient would |
| mitmproxy on a secondary thread | OK — HTTP 200 and HTTPS 200 through the proxy, 2 flows captured, clean shutdown |
| Scheduler (Task Scheduler) | OK — task created, fired, run logged, log in UTF-8, deletion verified |
| Capture Firefox | OK — locates it via the registry and launches it with `-no-remote -profile` |
| Destructive-command guard | OK — `del`, `Remove-Item`, `rd`, `format`, `robocopy /MIR`, `reg delete`, `shutdown` ask for confirmation |
| Emoji in a cp850 console | OK — previously died with `UnicodeEncodeError` on the first line |
| Test suite | 48 passed |
| `.exe` with PyInstaller | Starts and opens the UI |

## Port architecture

Everything that differs between systems goes through **`core/plataforma.py`**.
The rule when touching this code: if something changes by OS, the branch goes
there, not in the UI.

New modules:

- **`core/plataforma.py`** (expanded) — console encoding, read whitelist, per-OS
  prompt texts, Firefox location, DPI, Windows destructive patterns.
- **`core/interprete.py`** — decides which Python to use. The only place that
  knows about `sys.frozen`; without this, packaging the app makes
  `ejecutar_python` relaunch the whole application instead of running the script.
- **`core/consola.py`** — UTF-8 output at the console entry points.
- **`core/programador.py`** — became the common front. The backends are
  `programador_launchd.py` (macOS), `programador_schtasks.py` (Windows) and
  `programador_nulo.py` (the rest). The public interface didn't change, so
  `main_ui.py` wasn't touched for this.
- **`core/win_icono.py`** — taskbar icon + AppUserModelID. Mirror of
  `mac_icono.py`, same best-effort contract.

## Decisions worth knowing before touching this

**The scheduler uses XML, not `schtasks /SC`.** The day abbreviations for
`/SC WEEKLY /D` are **localized**: on a Spanish Windows they aren't
`MON,TUE,WED` but `LUN,MAR,MIÉ`. With `/SC`, weekly tasks would be created wrong
or fail outright, with an error that doesn't mention the language. The XML is
language-independent and also lets you turn off `DisallowStartIfOnBatteries`,
which comes as **true**: without that, on a laptop on battery the task never
runs. The XML is written in **UTF-16 with BOM**; in UTF-8, `schtasks` rejects it
with a "The task XML is malformed" that doesn't help.

**There's no wrapper `.cmd` for the tasks.** `schtasks` can't pass environment
variables, and the obvious fix (a `.cmd` that sets them) is a console process: it
would flash a black window at the person on every run. Instead, `correr_tarea.py`
accepts `--datos` and `--app`, and it's launched with `pythonw.exe`.

**The capture certificate does NOT install itself on Windows.** The NSS
`certutil` —the one that writes to Firefox's `cert9.db`— doesn't ship with
Firefox, and the `certutil.exe` on the PATH is Microsoft's, with different
syntax. The default path is the manual install via mitm.it, whose scope is the
disposable profile. `core/proxy.py:confiar_ca_en_windows()` offers the automatic
alternative (user store + `security.enterprise_roots`), but it's a separate
button on purpose: it makes **all** of that account's applications trust the
mitmproxy CA, a considerably larger scope than on macOS.
`quitar_ca_de_windows()` reverts it.

**On Windows there's no privilege elevation.** `ejecutar_como_admin` remains
macOS-only and the "Admin" checkbox is hidden. Telling the model about a
capability it doesn't have only produces rejected requests.

## Preexisting bugs fixed along the way

They weren't part of the port, but they were in the path:

1. **The zip shipped `.requisitos-instalados`.** Its hash matched the current
   `requirements.txt`, so on a fresh machine `bootstrap.py` skipped `pip install`
   and then died at verification. Now the marker lives inside `venv/` or
   `runtime/`, which never travel.
2. **`crear_zip()` always failed, on any system.** `ARCHIVOS_REQUERIDOS` demanded
   files from the `.app` bundle that don't exist in this tree.
3. **The destructive-command guard didn't cover Windows.** `plataforma.py` had
   the list written from before, but `analizar_riesgo()` never called it: `del`
   and `Remove-Item` ran without asking.
4. **`crear_zip()` built invalid zips on Windows.** The entries came out with
   `\`; unzipped on macOS it produced files named `AgentFactory\core\chat.py`.
5. **Diagnostics warned falsely.** On Windows `chmod(0o600)` "works" but reports
   `0o666`, so it said "other users can read your keys" on every startup.
6. **`crear_zip_portable()` existed without a button.** Now it's the button that
   takes the place of the `.dmg` outside macOS.

## About the `.exe`

It's built with:

```
runtime\python\python.exe -m PyInstaller --noconfirm --onedir --windowed ^
  --name AgentFactory --icon icono.ico ^
  --collect-all mitmproxy --collect-all cryptography ^
  --add-data "icono.png;." --add-data "icono.ico;." ^
  --add-data "requirements.txt;." --add-data "VERSION;." --add-data "config;config" ^
  main_ui.py
```

And **you have to copy an interpreter next to it**, in
`dist/AgentFactory/runtime/python/`: the app needs a real Python for
`ejecutar_python`, `pip` and scheduled tasks, and PyInstaller's embedded
interpreter is no good for that. Without it, `core/interprete.py` returns the
`SIN_INTERPRETE` sentinel instead of falling back to the Store stub.

Two things that were costly and worth not rediscovering:

- `main_ui._resolver_instalacion()` and `rutas.dir_app()` checked that
  `core/chat.py` existed **as a file**. Frozen it doesn't exist: the modules live
  inside the executable. The app showed an error box and quit. Both now have a
  `sys.frozen` branch.
- `--onedir` and not `--onefile`: onefile re-extracts ~240 MB into `%TEMP%` on
  every launch and trips antivirus heuristics.

**It's still worse than the portable folder**: 239 MB against 1.1 MB, it isn't
signed (SmartScreen anyway), and the version history (`core/versiones.py`) doesn't
work because it tracks `.py` files on disk. It's provided in case a double-click
without an initial download is needed, but the recommendation is the zip.

## What's still pending

- **Task notifications on Windows.** The native toast needs a registered
  AppUserModelID and a trip through WinRT. For now the result of each run shows on
  the Tasks tab.
- **Visual review of the UI.** `main_ui.py` has pixel measurements tuned on a
  Mac; with DPI awareness it's no longer blurry, but at 150% scale it's worth
  reviewing carefully. Escape hatch: `AGENTE_DPI=0`.
- **Linux.** `programador_nulo.py` returns a clear error instead of pretending.
- **`.msi`.** Not done. It would come from wrapping the `.onedir` with WiX or Inno
  Setup; it only makes sense if you want a "real" install into Program Files,
  which would also require signing the binary.
