# AgentFactory en Linux — estado

El soporte para Linux (Debian/Ubuntu, Fedora/RHEL, openSUSE y derivados) **está
escrito y probado en todo lo verificable desde macOS**, pero **todavía no se
corrió en una máquina Linux real**. Este documento dice qué hay y qué falta
confirmar, para cerrarlo en un Debian y un RHEL de verdad.

## Cómo se corre

Un solo camino, portable:

```
git clone https://github.com/destiladosJuncal/AgentFactory.git
cd AgentFactory
./INICIAR.sh
```

`INICIAR.sh` busca un Python 3.12+ **con tkinter**; si no lo hay, baja uno
(~30 MB, `python-build-standalone`) a `runtime/` dentro de la carpeta, instala
`requirements.txt` y abre la app. No instala nada en el sistema ni pide sudo.
Debian y RHEL comparten todo lo que importa (bash + systemd), así que **el mismo
build cubre ambos** — no hay ramas por distro.

## Arquitectura del port

Todo lo que difiere por SO ya vivía en `core/plataforma.py` y en los backends
del scheduler; el "resto" (Linux) estaba casi listo. Lo que se agregó:

- **`INICIAR.sh`** — arranque portable (arch x86_64 / aarch64, descarga con
  curl o wget). Espejo de `INICIAR.command`.
- **`core/programador_systemd.py`** — backend del planificador vía **timers de
  usuario** (`systemctl --user`), sin sudo. Intervalo → `OnUnitActiveSec`;
  diario/semanal → `OnCalendar` (días en inglés, independiente del idioma, a
  diferencia de schtasks en Windows).
- **`core/programador.py`** — el dispatcher ahora elige systemd en Linux.
- **`correr_tarea.py`** — notificaciones con `notify-send` (libnotify).
- **`core/plataforma.py`** — `ES_LINUX`. Lo demás (xdg-open, `which("firefox")`,
  shell bash, permisos POSIX, consola UTF-8, sin DPI) ya contemplaba Linux.

`lanzar_firefox` y `interprete()` ya soportaban Linux; no se tocaron.

## Verificado desde macOS

- 123 tests en verde (suite completa).
- Generación de units systemd correcta para intervalo, diario y semanal
  (`Mon,Wed,Fri *-*-* 08:30:00`).
- Sintaxis de `INICIAR.sh` (`bash -n`) y de todos los módulos.
- El dispatcher resuelve el backend por SO sin romper imports.

## Qué falta confirmar en Linux real (Debian **y** RHEL)

1. **tkinter del runtime abre.** El `python-build-standalone` trae tcl/tk, pero
   tk hace dlopen de libs de X11 del sistema. En un escritorio suelen estar; si
   no, `INICIAR.sh` ya imprime el `apt`/`dnf` exacto que falta. Confirmar que la
   UI abre en ambos.
2. **systemd de usuario.** `systemctl --user` requiere sesión de usuario (en un
   contenedor pelado puede no haber). Verificar: crear una tarea, que aparezca
   `enable --now`, que dispare, que registre la corrida, y que `borrar` limpie
   los units. Para que corra con el usuario **deslogueado**:
   `loginctl enable-linger $USER` (una vez) — documentarlo en la UI si hace
   falta.
3. **Firefox de captura.** `which("firefox")` + `-no-remote -profile`. En
   distros donde Firefox es un **Snap/Flatpak**, el `-profile` a una carpeta
   arbitraria puede no respetarse por el sandbox: probar, y si molesta, preferir
   el paquete nativo o documentar la limitación.
4. **Wayland vs X11.** tkinter corre sobre XWayland sin problema en general;
   confirmar que la ventana no sale borrosa en HiDPI (no hay compensación de DPI
   en Linux por ahora, igual que macOS).
5. **notify-send** presente (viene con `libnotify-bin` en Debian). Best-effort:
   si falta, la corrida igual queda en la pestaña Tareas.

## Pendiente conocido

- **HiDPI en Linux.** Sin escalado explícito, como en macOS. Si se ve chico al
  200%, se resuelve igual que se hará en Mac (un `tk scaling`).
- **Empaquetado nativo (.deb/.rpm/AppImage).** No hay; la recomendación es la
  carpeta portable, igual que en las otras plataformas. Un `.desktop` para que
  aparezca en el menú de aplicaciones sería el siguiente paso.
