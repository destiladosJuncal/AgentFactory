# AgentFactory on Linux — status

Linux support (Debian/Ubuntu, Fedora/RHEL, openSUSE and derivatives) is
**written and tested for everything verifiable from macOS**, but has **not yet
been run on a real Linux machine**. This document says what's there and what's
left to confirm, so it can be closed out on a real Debian and a real RHEL.

## How it runs

A single, portable path:

```
git clone https://github.com/destiladosJuncal/AgentFactory.git
cd AgentFactory
./INICIAR.sh
```

`INICIAR.sh` looks for a Python 3.12+ **with tkinter**; if there isn't one, it
downloads one (~30 MB, `python-build-standalone`) to `runtime/` inside the
folder, installs `requirements.txt` and opens the app. It installs nothing on the
system and asks for no sudo. Debian and RHEL share everything that matters
(bash + systemd), so **the same build covers both** — there are no per-distro
branches.

## Port architecture

Everything that differs by OS already lived in `core/plataforma.py` and in the
scheduler backends; the "rest" (Linux) was almost ready. What was added:

- **`INICIAR.sh`** — portable launch (x86_64 / aarch64 arch, download with curl
  or wget). Mirror of `INICIAR.command`.
- **`core/programador_systemd.py`** — scheduler backend via **user timers**
  (`systemctl --user`), no sudo. Interval → `OnUnitActiveSec`; daily/weekly →
  `OnCalendar` (days in English, language-independent, unlike schtasks on
  Windows).
- **`core/programador.py`** — the dispatcher now picks systemd on Linux.
- **`correr_tarea.py`** — notifications with `notify-send` (libnotify).
- **`core/plataforma.py`** — `ES_LINUX`. The rest (xdg-open, `which("firefox")`,
  bash shell, POSIX permissions, UTF-8 console, no DPI) already accounted for
  Linux.

`lanzar_firefox` and `interprete()` already supported Linux; they weren't touched.

## Verified from macOS

- 123 tests green (full suite).
- Correct systemd unit generation for interval, daily and weekly
  (`Mon,Wed,Fri *-*-* 08:30:00`).
- Syntax of `INICIAR.sh` (`bash -n`) and of all modules.
- The dispatcher resolves the backend by OS without breaking imports.

## What's left to confirm on real Linux (Debian **and** RHEL)

1. **The runtime's tkinter opens.** `python-build-standalone` ships tcl/tk, but
   tk dlopens the system's X11 libs. On a desktop they're usually there; if not,
   `INICIAR.sh` already prints the exact `apt`/`dnf` that's missing. Confirm the
   UI opens on both.
2. **User systemd.** `systemctl --user` requires a user session (a bare container
   may not have one). Verify: create a task, that `enable --now` shows up, that
   it fires, that it logs the run, and that `delete` cleans up the units. For it
   to run with the user **logged out**: `loginctl enable-linger $USER` (once) —
   document it in the UI if needed.
3. **Capture Firefox.** `which("firefox")` + `-no-remote -profile`. On distros
   where Firefox is a **Snap/Flatpak**, `-profile` to an arbitrary folder may not
   be honored due to the sandbox: test it, and if it's a problem, prefer the
   native package or document the limitation.
4. **Wayland vs X11.** tkinter runs on XWayland fine in general; confirm the
   window doesn't come out blurry on HiDPI (there's no DPI compensation on Linux
   for now, same as macOS).
5. **notify-send** present (ships with `libnotify-bin` on Debian). Best-effort:
   if it's missing, the run still lands on the Tasks tab.

## Known pending items

- **HiDPI on Linux.** No explicit scaling, as on macOS. If it looks small at
  200%, it's solved the same way it will be on Mac (a `tk scaling`).
- **Native packaging (.deb/.rpm/AppImage).** None; the recommendation is the
  portable folder, same as on the other platforms. A `.desktop` so it shows up in
  the applications menu would be the next step.
