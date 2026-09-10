# AgentFactory — handoff para build en Windows

Este documento le da a una sesión NUEVA de Claude Code (en Windows) todo el
contexto de AgentFactory, para generar los binarios sin depender de la
conversación original (que vive en otra máquina y no se sincroniza).

## Qué es AgentFactory

App de escritorio (Python + Tkinter) que es un **fork independiente** de
"AgenteDeepSeek". Un asistente conversacional que además:
- Captura TODO el tráfico web que el usuario navega (proxy mitmproxy → SQLite),
  sin marcar nada, para que el agente **infiera de qué sitio se le habla** y
  **extraiga** lo navegado (ej: trabajos/perfiles de LinkedIn → JSON/PDF).
- Programa tareas recurrentes (el "cron") que corren solas y guardan su
  resultado, con historial de corridas.

## Estado actual (lo hecho hasta ahora)

- **UI** (`main_ui.py`, Tkinter): pestañas **Conversaciones**, **Tareas**,
  **Configuración**. Otras (Proyectos, Biblioteca, Procesador, Proxy) se
  construyen pero están ocultas. Chat con globitos estilo WhatsApp. Ventana
  maximizada al abrir. Botón **🦊 Abrir Firefox** (captura) en la cabecera.
- **Captura** (`core/proxy.py`): mitmproxy → `sesion.db` (SQLite) con cada
  flujo (host, método, ruta, headers, bodies…). Tools del agente para
  inferir sitio y extraer: `listar_sitios_capturados`, `buscar_en_captura`,
  `ver_cuerpo_flujo` (en `core/proxy_tool.py`).
- **Cron** (`core/programador.py` + `correr_tarea.py`): en macOS usa **launchd**
  (LaunchAgents). Cada corrida se registra (`_tareas/<id>.runs.json`) y se ve
  en la pestaña Tareas (lista de corridas + resultado).
- **Marca**: logo = llama de Prometeo + engranaje violeta→magenta
  (`agentfactory-icon-1024.png`, `AppIcon.icns`). Paleta en `PALETA.md`.
- **Arranque portable** (macOS): `INICIAR.command` (bash) busca/baja un Python
  con tkinter y delega en `bootstrap.py` (crea venv, instala `requirements.txt`,
  lanza `main_ui.py`). El `.app` se arma con `construir_app.sh`.
- **Datos** separados del código: `AGENTE_DATOS` (por defecto `~/tmp/agentfactory`)
  guarda conversaciones, `.env` con las API keys, captura y tareas. El código
  resuelve rutas en `core/rutas.py` (soporta `AGENTE_DATOS`/`AGENTE_APP`).

## Lo que falta para Windows (la tarea)

1. **Arranque portable Windows**: un `INICIAR.bat` equivalente al
   `INICIAR.command` — que encuentre/baje un Python con tkinter (el standalone
   de `python-build-standalone` para `x86_64-pc-windows-msvc`) y corra
   `bootstrap.py`.
2. **`bootstrap.py` multiplataforma**: hoy asume `venv/bin/python3` (POSIX). En
   Windows el intérprete del venv es `venv\Scripts\python.exe`. Ajustar
   `PY_VENV` según `os.name`/`sys.platform`.
3. **Scheduler Windows**: `core/programador.py` usa launchd. En Windows hay que
   usar **Task Scheduler** (`schtasks /Create /SC DAILY /TN ... /TR ...`), o
   mantener la misma interfaz (`crear_tarea`/`listar_tareas`/`borrar_tarea`)
   con backend por-OS.
4. **Firefox de captura** (`core/proxy.py:lanzar_firefox`): hoy asume
   `/Applications/Firefox.app` y `open -n -a`. En Windows: localizar
   `firefox.exe` (Program Files) y lanzarlo con `-no-remote -profile <perfil>`.
5. **Notificaciones**: `correr_tarea.py` usa `osascript`. En Windows usar
   PowerShell (toast) o `win10toast`.
6. **Binario**: opción A (recomendada primero) = distribuir la carpeta con
   `INICIAR.bat` portable (sin compilar). Opción B = un `.exe` con **PyInstaller**
   (`pyinstaller --onefile --windowed main_ui.py` + hidden imports de mitmproxy;
   ojo: mitmproxy y tkinter piden configuración extra). El `.exe` de Windows
   **hay que compilarlo EN Windows** (PyInstaller no cross-compila desde macOS).

## Dependencias (`requirements.txt`)

openai, psutil, pytest, python-dotenv, requests, anthropic, Pillow, mitmproxy.
El `.env` con las keys NO viaja en el código (va en `AGENTE_DATOS`).

## Primer paso sugerido en Windows

Abrir esta carpeta en Claude Code y pedir: *"leé HANDOFF-WINDOWS.md y generá el
arranque portable para Windows (INICIAR.bat + bootstrap multiplataforma), después
vemos el .exe"*.
