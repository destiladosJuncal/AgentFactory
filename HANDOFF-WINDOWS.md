# AgentFactory en Windows — estado

El port a Windows **está hecho y probado** en Windows 10 Pro 19045 (es-ES),
sobre una máquina **sin Python instalado**. Este documento reemplaza al handoff
original, que describía la tarea pendiente.

## Cómo se instala en otra Windows

**Camino recomendado — carpeta portable (1,1 MB):**

1. Descomprimir `AgentFactory-windows.zip` entero en una carpeta del usuario.
2. Doble clic en `INICIAR.bat`.

La primera vez baja un Python 3.12.13 (~30 MB) de `python-build-standalone`, lo
deja en `runtime/` adentro de la misma carpeta, instala `requirements.txt` y
abre la app. No instala nada en el sistema, no pide administrador y no toca el
registro. Los arranques siguientes son directos y sin consola.

**Camino alternativo — `.exe` (239 MB, `dist/AgentFactory/`):** ver más abajo.

## Qué se probó, y con qué resultado

| | |
|---|---|
| Arranque en máquina sin Python | OK — detecta y descarta el stub de la Microsoft Store, baja el runtime, instala y abre |
| Instalación desde el zip en carpeta limpia | OK — probado descomprimiendo y arrancando como lo haría quien lo recibe |
| mitmproxy en un hilo secundario | OK — HTTP 200 y HTTPS 200 a través del proxy, 2 flujos capturados, apagado limpio |
| Planificador (Task Scheduler) | OK — tarea creada, disparada, corrida registrada, log en UTF-8, borrado verificado |
| Firefox de captura | OK — lo ubica por el registro y lo lanza con `-no-remote -profile` |
| Guard de comandos destructivos | OK — `del`, `Remove-Item`, `rd`, `format`, `robocopy /MIR`, `reg delete`, `shutdown` piden confirmación |
| Emoji en consola cp850 | OK — antes moría con `UnicodeEncodeError` en la primera línea |
| Suite de tests | 48 passed |
| `.exe` con PyInstaller | Arranca y abre la UI |

## Arquitectura del port

Todo lo que difiere entre sistemas pasa por **`core/plataforma.py`**. La regla
al tocar este código: si algo cambia según el SO, la rama va ahí, no en la UI.

Módulos nuevos:

- **`core/plataforma.py`** (ampliado) — codificación de consola, whitelist de
  lectura, textos del prompt por SO, ubicación de Firefox, DPI, patrones
  destructivos de Windows.
- **`core/interprete.py`** — decide qué Python usar. Único lugar que sabe de
  `sys.frozen`; sin esto, empaquetar la app hace que `ejecutar_python` relance
  la aplicación entera en vez de correr el script.
- **`core/consola.py`** — salida en UTF-8 en los puntos de entrada de consola.
- **`core/programador.py`** — pasó a ser el frente común. Los backends son
  `programador_launchd.py` (macOS), `programador_schtasks.py` (Windows) y
  `programador_nulo.py` (resto). La interfaz pública no cambió, así que
  `main_ui.py` no se tocó para esto.
- **`core/win_icono.py`** — ícono de barra de tareas + AppUserModelID. Espejo
  de `mac_icono.py`, mismo contrato best-effort.

## Decisiones que conviene conocer antes de tocar esto

**El planificador usa XML, no `schtasks /SC`.** Las abreviaturas de día de
`/SC WEEKLY /D` están **localizadas**: en un Windows en español no son
`MON,TUE,WED` sino `LUN,MAR,MIÉ`. Con `/SC` las tareas semanales se crearían
mal o directamente fallarían, con un error que no menciona el idioma. El XML es
independiente del idioma y además permite apagar `DisallowStartIfOnBatteries`,
que viene en **true**: sin eso, en una laptop a batería la tarea nunca corre.
El XML se escribe en **UTF-16 con BOM**; en UTF-8, `schtasks` lo rechaza con un
"The task XML is malformed" que no ayuda.

**No hay un `.cmd` envolvente para las tareas.** `schtasks` no sabe pasar
variables de entorno, y la solución obvia (un `.cmd` que las setee) es un
proceso de consola: le haría parpadear una ventana negra a la persona en cada
corrida. En vez de eso, `correr_tarea.py` acepta `--datos` y `--app`, y se lo
lanza con `pythonw.exe`.

**El certificado de la captura NO se instala solo en Windows.** El `certutil`
de NSS —el que escribe en el `cert9.db` de Firefox— no viene con Firefox, y el
`certutil.exe` del PATH es el de Microsoft, con otra sintaxis. El camino por
defecto es la instalación manual por mitm.it, cuyo alcance es el perfil
descartable. `core/proxy.py:confiar_ca_en_windows()` ofrece la alternativa
automática (almacén del usuario + `security.enterprise_roots`), pero es un
botón aparte a propósito: hace que **todas** las aplicaciones de esa cuenta
confíen en la CA de mitmproxy, un alcance bastante mayor que el de macOS.
`quitar_ca_de_windows()` lo revierte.

**En Windows no hay elevación de privilegios.** `ejecutar_como_admin` sigue
siendo solo de macOS y el checkbox "Admin" se oculta. Contarle al modelo de una
capacidad que no tiene solo produce pedidos rechazados.

## Bugs preexistentes que se arreglaron de paso

No eran del port, pero estaban en el camino:

1. **El zip traía `.requisitos-instalados`.** Su hash coincidía con el
   `requirements.txt` vigente, así que en una máquina nueva `bootstrap.py`
   salteaba `pip install` y moría después en la verificación. Ahora el sello
   vive adentro de `venv/` o `runtime/`, que nunca viajan.
2. **`crear_zip()` fallaba siempre, en cualquier sistema.** `ARCHIVOS_REQUERIDOS`
   exigía archivos del bundle `.app` que no existen en este árbol.
3. **El guard de comandos destructivos no cubría Windows.** `plataforma.py`
   tenía la lista escrita desde antes, pero `analizar_riesgo()` nunca la
   llamaba: `del` y `Remove-Item` se ejecutaban sin preguntar.
4. **`crear_zip()` armaba zips inválidos en Windows.** Las entradas salían con
   `\`; descomprimido en macOS daba archivos llamados `AgentFactory\core\chat.py`.
5. **El diagnóstico avisaba en falso.** En Windows `chmod(0o600)` "funciona"
   pero reporta `0o666`, así que decía "otros usuarios pueden leer tus claves"
   en cada arranque.
6. **`crear_zip_portable()` existía sin botón.** Ahora es el botón que ocupa el
   lugar del `.dmg` fuera de macOS.

## Sobre el `.exe`

Se arma con:

```
runtime\python\python.exe -m PyInstaller --noconfirm --onedir --windowed ^
  --name AgentFactory --icon icono.ico ^
  --collect-all mitmproxy --collect-all cryptography ^
  --add-data "icono.png;." --add-data "icono.ico;." ^
  --add-data "requirements.txt;." --add-data "VERSION;." --add-data "config;config" ^
  main_ui.py
```

Y **hay que copiarle un intérprete al lado**, en
`dist/AgentFactory/runtime/python/`: la app necesita un Python real para
`ejecutar_python`, `pip` y las tareas programadas, y el intérprete embebido de
PyInstaller no sirve para eso. Sin él, `core/interprete.py` devuelve el
centinela `SIN_INTERPRETE` en vez de caer en el stub de la Store.

Dos cosas que costaron y conviene no volver a descubrir:

- `main_ui._resolver_instalacion()` y `rutas.dir_app()` verificaban que
  existiera `core/chat.py` **como archivo**. Congelado no existe: los módulos
  viven adentro del ejecutable. La app mostraba un cuadro de error y salía.
  Ambos tienen ahora una rama `sys.frozen`.
- `--onedir` y no `--onefile`: onefile reextrae ~240 MB en `%TEMP%` en cada
  arranque y dispara heurísticas de antivirus.

**Sigue siendo peor que la carpeta portable**: 239 MB contra 1,1 MB, no está
firmado (SmartScreen igual), y el historial de versiones (`core/versiones.py`)
no funciona porque rastrea archivos `.py` en disco. Se entrega por si hace
falta un doble clic sin descarga inicial, pero la recomendación es el zip.

## Lo que queda pendiente

- **Notificaciones de tareas en Windows.** El toast nativo necesita un
  AppUserModelID registrado y una vuelta por WinRT. Por ahora el resultado de
  cada corrida se ve en la pestaña Tareas.
- **Repaso visual de la interfaz.** `main_ui.py` tiene medidas en píxeles
  ajustadas en una Mac; con DPI awareness ya no se ve borrosa, pero al 150% de
  escala conviene revisarla con calma. Escape: `AGENTE_DPI=0`.
- **Linux.** `programador_nulo.py` devuelve un error claro en vez de fingir.
- **`.msi`.** No se hizo. Saldría de envolver el `.onedir` con WiX o Inno
  Setup; solo tiene sentido si se quiere una instalación "de verdad" en
  Archivos de programa, que además exigiría firmar el binario.
