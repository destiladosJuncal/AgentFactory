# AgentFactory

Asistente conversacional de escritorio (Python + Tkinter) que además:

- **Captura el tráfico web** que navegás, a través de un proxy propio
  (mitmproxy → SQLite), para que el agente pueda inferir de qué sitio le estás
  hablando y extraer lo que viste.
- **Programa tareas recurrentes** que corren solas, con la app cerrada, y
  guardan su resultado con historial de corridas.
- Habla con varios proveedores de modelos: DeepSeek, Claude, Qwen y Gemini.

Funciona en **Windows 10/11** y en **macOS** (Apple Silicon e Intel) desde el
mismo código.

---

## Instalación

**No hace falta tener Python instalado.** Si la máquina no tiene uno usable, el
arrancador baja uno (~30 MB) y lo deja adentro de la carpeta del proyecto. No
instala nada en el sistema, no pide permisos de administrador y no toca el
registro: si borrás la carpeta, no queda rastro.

```
git clone https://github.com/destiladosJuncal/AgentFactory.git
cd AgentFactory
```

Y después, según la plataforma:

### Windows 10 / 11 (64 bits)

Doble clic en **`INICIAR.bat`**, o desde una consola:

```
INICIAR.bat
```

La primera vez baja Python, instala las dependencias y abre la app; tarda un
par de minutos y necesita internet. Los arranques siguientes son directos y
sin consola.

Si clonaste con el Explorador de Windows en vez de `git`, puede que Windows
marque el `.bat` como descargado: clic derecho → *Propiedades* → **Desbloquear**.
Y si aparece *"Windows protegió tu PC"* (SmartScreen), es porque el archivo no
está firmado: *Más información* → *Ejecutar de todas formas*.

### macOS (Apple Silicon o Intel)

Doble clic en **`INICIAR.command`**, o desde la Terminal:

```
./INICIAR.command
```

Si clonaste el repo, el bit de ejecución ya viene puesto. Si en cambio bajaste
un zip, hace falta darlo una vez:

```
chmod +x INICIAR.command
```

La primera vez macOS puede decir que no puede verificar al desarrollador
(el script no está firmado con un Apple Developer ID): clic derecho sobre
**`INICIAR.command`** → **Abrir** → Abrir.

### Requisitos

- Windows 10 versión 1803 o posterior (hace falta `tar.exe`, que viene con el
  sistema desde entonces) / Windows 11, de 64 bits. O macOS con Apple Silicon
  o Intel.
- Conexión a internet la primera vez.
- Firefox, **solo** si vas a usar la captura de tráfico.

---

## Después de instalar

La app abre en **⚙️ Configuración** porque todavía no hay ninguna API key
cargada. Poné la del proveedor que uses y apretá **Probar**: hace una llamada
real y te dice qué pasó, en vez de fallar recién cuando quieras conversar.

### Dónde quedan tus datos

Las conversaciones, tareas, la biblioteca y el `.env` con las API keys viven
**fuera** de la carpeta del código:

| | |
|---|---|
| Windows | `%USERPROFILE%\tmp\agentfactory\` |
| macOS | `~/tmp/agentfactory/` |

Es deliberado: actualizar el código con un `git pull` no toca tus datos, y
compartir o publicar la carpeta del proyecto no se lleva tus claves. Se puede
cambiar con la variable `AGENTE_DATOS`.

---

## Cómo está organizado

Todo lo que difiere entre sistemas operativos pasa por **`core/plataforma.py`**.
Si vas a tocar este código, la regla es: la rama por-SO va ahí, no desperdigada
en la interfaz.

| | |
|---|---|
| `INICIAR.bat` / `INICIAR.command` | Arranque portable por plataforma: consiguen un Python usable |
| `bootstrap.py` | Entorno virtual, dependencias y lanzamiento (multiplataforma) |
| `main_ui.py` | La interfaz (Tkinter) |
| `core/plataforma.py` | Única capa que sabe de diferencias entre sistemas |
| `core/interprete.py` | Qué Python usar para correr scripts (incluye el caso empaquetado) |
| `core/programador*.py` | Tareas recurrentes: frente común + backend launchd / Task Scheduler |
| `core/proxy.py` | Captura de tráfico y el Firefox dedicado |
| `core/ejecucion.py` | Ejecución de comandos, con confirmación de lo destructivo |

Detalles del port a Windows, decisiones tomadas y lo que queda pendiente:
[`HANDOFF-WINDOWS.md`](HANDOFF-WINDOWS.md).

## Tests

```
# Windows
runtime\python\python.exe -m pytest tests\ -q

# macOS
runtime/python/bin/python3 -m pytest tests/ -q
```

## Empaquetar para compartir

Desde la pestaña **⚙️ Configuración**, el botón **Crear paquete portable
(.zip)** arma un zip solo con el código: deja afuera el entorno, el runtime y
tus datos, y además revisa el contenido buscando credenciales — si encuentra
algo que parece una clave, no genera el archivo.

En macOS hay además un botón para armar un `.dmg`. En Windows se puede armar un
`.exe` con PyInstaller; las instrucciones y sus limitaciones están en
[`HANDOFF-WINDOWS.md`](HANDOFF-WINDOWS.md), pero para distribuir conviene el
zip portable o directamente este repo.

## Seguridad

Dos cosas que vale la pena saber antes de usarlo:

- **El agente ejecuta comandos reales en tu máquina.** No es un sandbox y no
  pretende serlo. El freno es el que importa: todo lo que borre o sobrescriba
  archivos se detiene y te pregunta, en los dos sistemas.
- **La captura de tráfico intercepta HTTPS.** Usa un perfil de Firefox aparte
  y descartable, para no tocar tu navegador de todos los días. En Windows hay
  además un botón opcional para que la CA valga en todo el sistema; está
  separado a propósito, porque su alcance es mucho mayor, y es reversible.
