# AgentFactory

Asistente conversacional de escritorio (Python + Tkinter) que corre en
**Windows 10/11** y en **macOS** (Apple Silicon e Intel) desde el mismo código.

No es un chat con un modelo y nada más. Las tres cosas que lo distinguen:

- **Construye herramientas y se las queda.** El modo iterativo escribe código,
  lo corre, lo evalúa con un puntaje y reintenta hasta que funciona. Lo que
  sale bien se publica en una biblioteca compartida y queda disponible para la
  próxima conversación.
- **Captura lo que navegás.** Un proxy propio (mitmproxy → SQLite) registra el
  tráfico web, así el agente puede inferir de qué sitio le hablás y extraer lo
  que viste, sin que tengas que copiar y pegar nada.
- **Se despierta solo.** Las tareas programadas corren con la app cerrada, por
  el planificador del sistema, y guardan su resultado con historial.

Habla con DeepSeek, Claude, Qwen y Gemini.

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

Si aparece *"Windows protegió tu PC"* (SmartScreen), es porque el archivo no
está firmado: *Más información* → *Ejecutar de todas formas*. Y si bajaste un
zip en vez de clonar, puede que Windows marque el `.bat` como descargado: clic
derecho → *Propiedades* → **Desbloquear**.

### macOS (Apple Silicon o Intel)

Doble clic en **`INICIAR.command`**, o desde la Terminal:

```
./INICIAR.command
```

Si clonaste el repo, el bit de ejecución ya viene puesto. Si bajaste un zip,
hace falta darlo una vez: `chmod +x INICIAR.command`.

La primera vez macOS puede decir que no puede verificar al desarrollador (el
script no está firmado con un Apple Developer ID): clic derecho sobre
**`INICIAR.command`** → **Abrir** → Abrir.

### Requisitos

- Windows 10 versión 1803 o posterior (hace falta `tar.exe`, que viene con el
  sistema desde entonces) / Windows 11, de 64 bits. O macOS con Apple Silicon
  o Intel.
- Conexión a internet la primera vez.
- Firefox, **solo** si vas a usar la captura de tráfico.

### Primer arranque

La app abre en **⚙️ Configuración** porque todavía no hay ninguna API key
cargada. Poné la del proveedor que uses y apretá **Probar**: hace una llamada
real y te dice qué pasó, en vez de fallar recién cuando quieras conversar.

---

## Qué hace

### Conversaciones

Cada conversación es una carpeta con su historial, su workspace y sus permisos.
El agente tiene **21 herramientas**: leer y escribir archivos, ejecutar shell y
Python, instalar paquetes, consultar la captura del proxy, y las de la
biblioteca y el modo iterativo que se describen abajo.

La transcripción se renderiza como Markdown: tablas, código con botón de
ejecutar, e imágenes mostradas en línea. Abajo de cada mensaje quedan los
tokens y el costo estimado de la llamada.

### La biblioteca compartida

El problema que resuelve: sin ella, cada conversación arranca de cero y el
agente reconstruye una y otra vez las mismas cosas.

Cuando el agente construye algo que funciona —un extractor, un wrapper de una
API, un conversor— lo **publica** en la biblioteca, que vive fuera de cualquier
proyecto. En la conversación siguiente puede listarla, leer un módulo para ver
qué funciones expone, y ejecutarlo, en vez de escribirlo de nuevo.

Las cuatro herramientas: `listar_biblioteca`, `leer_modulo_biblioteca`,
`publicar_modulo_biblioteca`, `ejecutar_modulo_biblioteca`.

El flujo que el prompt del sistema le pide seguir cuando le encargás una
acción: primero mirar la biblioteca; si hay algo que sirve, usarlo; si no
existe, construirlo con el modo iterativo y publicarlo. Así la biblioteca crece
con cada cosa que hacen juntos.

### El modo iterativo

Es el motor que **construye** herramientas, en vez de solo ejecutarlas. El
ciclo es: objetivo → generar código → correrlo → evaluarlo con un puntaje →
diagnosticar qué falló → reintentar.

El evaluador puntúa funcionalidad (¿pasan los tests?), eficiencia y calidad, y
devuelve un diagnóstico que entra en el prompt del reintento. Los objetivos y
el puntaje mínimo se configuran en `config/objetivos.json`.

Se puede usar de dos maneras:

- **Desde la conversación**, con la herramienta `iterar_codigo`: el agente
  dispara una corrida iterativa sin que salgas del chat. Corre en modo no
  interactivo (no puede pararse a preguntarte a mitad de camino) y tiene un
  tope de 10 iteraciones. Deliberadamente, esta herramienta **no** se le ofrece
  al generador de adentro, para que una corrida iterativa no pueda disparar
  otra recursivamente.
- **Como programa aparte**, con `main_interactivo.py`, que sí puede pausar y
  preguntarte entre iteraciones.

Los proyectos son persistentes: una corrida disparada desde el chat crea un
proyecto normal que después podés seguir iterando a mano.

### "Sí a todo"

Es más acotado de lo que suena, y conviene entender exactamente qué hace.

Cuando el agente corta a mitad de una tarea larga preguntando *"¿sigo?"*, con
esto activado se le responde solo, hasta un máximo de veces por cada mensaje
tuyo. Sirve para tareas de muchos pasos donde no querés estar apretando
"seguí".

**No desactiva ninguna confirmación de seguridad.** Los diálogos de borrado y
los de administrador son otro mecanismo y siguen preguntando siempre, con "Sí
a todo" activado o no. Lo único que automatiza es la pregunta de continuación.

Aparte de eso, en el diálogo de confirmación de un comando destructivo hay un
botón **"Permitir siempre"**, que es otra cosa: vale por *tipo* de operación
(aprobar un `rm` no aprueba un `git reset --hard`), solo para esa conversación,
y no se guarda en disco — al reabrir la app se vuelve a preguntar.

### Captura de tráfico

Con la captura encendida, el botón **🦊 Abrir Firefox** abre un Firefox con un
perfil aparte y descartable, ya configurado para pasar por el proxy. Tu Firefox
de todos los días queda intacto: sin proxy, sin la CA instalada.

Todo lo que navegás en esa ventana queda en una SQLite. Después, en el chat, el
agente puede preguntarle a la captura qué sitios hay, buscar flujos y leer
cuerpos para entender la estructura antes de escribir la extracción. Los flujos
se pueden **marcar** con una etiqueta y una nota para retomarlos, y hay un
**repetidor** para volver a disparar una request editada.

En la sección de seguridad está cómo se tratan tus credenciales de sesión
cuando automatizás sitios con login.

### Tareas programadas

El agente no puede despertarse solo: lo despierta el sistema. Cada tarea se
traduce en un LaunchAgent (macOS) o una tarea del Programador de tareas
(Windows) que, a la hora indicada o **cada N minutos**, corre sin que la app
esté abierta. No hace falta permisos de administrador en ninguna plataforma.

Hay **dos tipos de tarea**, y la diferencia importa:

- **Tarea-agente**: cada corrida le manda un prompt guardado al modelo. Para lo
  que necesita juicio o redacción en cada vuelta (resumir, decidir, escribir).
- **Tarea-script (script-first)**: para un workflow concreto y repetible —"cada
  10 min traeme el saldo", "todas las mañanas armá el reporte"— el agente
  escribe y prueba **un script Python determinístico** y lo registra como tarea.
  Ese script corre solo, **barato y sin gastar modelo**. El LLM vuelve a entrar
  **solo** como *fallback*, cuando el script no puede determinar el próximo paso
  (falla, o imprime una línea `ESCALAR: <motivo>`): ahí el agente lo arregla o
  reporta. Es el modelo por defecto para tareas recurrentes: se construye una
  vez con criterio, y después se ejecuta como código, no como conversación.

El agente arma las tarea-script desde el chat con la herramienta
`programar_tarea_script`. Cada corrida queda registrada con su resultado en la
pestaña **Tareas**, con un semáforo de salud (✅ sana · ⏸ ausente del sistema ·
❌ falló · 🕓 nunca corrió) y botones para **correrla ahora** (verificar sin
esperar la agenda) y **recargarla** en el planificador.

### Historial de versiones

**Ctrl+0** (⌘0 en Mac) abre el historial del código de la instalación. Cada
cambio queda registrado y se puede volver atrás. Es la red por si una
actualización rompe algo.

### Paquetes compartidos

Cuando el agente necesita numpy, pandas o lo que sea, se instala **una vez** en
un almacén compartido entre conversaciones, no un venv por conversación. Si una
conversación necesita una versión que choca con la compartida, esa versión se
instala solo para ella, sin romper a las demás. Separado por versión de Python,
porque los wheels con extensiones en C no son compatibles entre versiones.

### Costos

La app calcula el gasto por conversación a partir de una tabla de precios por
millón de tokens, que se puede actualizar desde Configuración. Si no hay precio
cargado para un modelo, muestra los tokens y el costo como `—` en vez de
inventar un número.

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
| `core/chat.py` | La conversación y el ciclo de tool calling |
| `core/biblioteca.py` | La biblioteca compartida de módulos |
| `core/generador.py` · `core/evaluador.py` | El motor iterativo y su puntaje |
| `core/programador*.py` | Tareas: frente común + backend launchd / Task Scheduler |
| `core/proxy.py` · `core/marcas.py` | Captura de tráfico, redacción y contexto |
| `core/ejecucion.py` | Ejecución de comandos, con confirmación de lo destructivo |

Detalles del port a Windows, decisiones tomadas y lo que queda pendiente:
[`HANDOFF-WINDOWS.md`](HANDOFF-WINDOWS.md).

### Dónde quedan tus datos

Las conversaciones, tareas, la biblioteca, la captura y el `.env` con las API
keys viven **fuera** de la carpeta del código:

| | |
|---|---|
| Windows | `%USERPROFILE%\tmp\agentfactory\` |
| macOS | `~/tmp/agentfactory/` |

Es deliberado: un `git pull` no toca tus datos, y publicar la carpeta del
proyecto no se lleva tus claves. Se cambia con la variable `AGENTE_DATOS`.

## Tests

```
# Windows
runtime\python\python.exe -m pytest tests\ -q

# macOS
runtime/python/bin/python3 -m pytest tests/ -q
```

## Empaquetar para compartir

Desde **⚙️ Configuración**, el botón **Crear paquete portable (.zip)** arma un
zip solo con el código: deja afuera el entorno, el runtime y tus datos, y
además revisa el contenido buscando credenciales — si encuentra algo que parece
una clave, no genera el archivo.

En macOS hay además un botón para armar un `.dmg`. En Windows se puede armar un
`.exe` con PyInstaller; las instrucciones y sus limitaciones están en
[`HANDOFF-WINDOWS.md`](HANDOFF-WINDOWS.md).

---

## Seguridad

Esta app le da a un modelo de lenguaje capacidades reales sobre tu máquina.
Vale la pena entender qué protege y qué no, sin optimismo.

### El agente ejecuta comandos reales

No es un sandbox y no pretende serlo: `ejecutar_shell` y `ejecutar_python`
pueden hacer lo que vos podrías hacer desde una terminal, sobre cualquier ruta
del sistema.

El freno es el que importa: **todo lo que borre o sobrescriba archivos se
detiene y te pregunta**, en los dos sistemas y con las listas de comandos
propias de cada uno (`rm`, `mv`, `dd`, `git reset --hard` en Unix; `del`,
`rd`, `Remove-Item`, `format`, `robocopy /MIR`, `reg delete`, `shutdown` en
Windows). Antes de aprobar, el diálogo te muestra sobre qué rutas concretas va
a operar.

Lo que **no** está protegido: leer. Un comando que solo lee no pregunta nada, y
el agente puede leer cualquier archivo al que tengas acceso.

La elevación de privilegios existe solo en macOS, y usa el diálogo de
autenticación del sistema (nunca uno propio: tu contraseña jamás llega a este
proceso). En Windows no está implementada y el control está oculto.

### La captura intercepta HTTPS

Para poder leer el tráfico, el proxy termina el TLS: instala su propia CA en un
perfil de Firefox descartable. Ese es el alcance por defecto, y es a propósito.

En Windows hay además un botón opcional para que la CA valga en todo el
sistema. Está separado y es reversible porque su alcance es mucho mayor: hace
que **todas** las aplicaciones de tu usuario confíen en esa CA.

### Cómo se manejan tus credenciales de sesión

Automatizar tareas en sitios con login es para lo que existe esta app, así que
tus cookies de sesión son parte del material con el que trabaja. Cómo las
trata:

**Lo que el modelo ve.** El contexto que se le pasa al modelo va redactado: las
cookies de sesión, `Authorization` y los tokens CSRF salen enmascarados
(`core/marcas.py`). La lectura en bloque para escribir una extracción
(`extraer_de_captura`) devuelve los cuerpos de las respuestas y **ningún
header**. La idea es que el modelo no necesita ver el valor de una cookie para
escribir el código que la usa.

Encima de eso hay un **filtro de salida** (`core/redactor.py`) que tapa lo que
`marcas.py` no atrapa: enmascara valores del `.env`, JWT, `Bearer` y
asignaciones tipo `password=` / `clave=` en **cualquier** texto que vaya al
modelo — incluidos los cuerpos capturados (donde puede ir una clave de login) y
la salida de `ejecutar_shell`/`ejecutar_python` (por si un comando imprime el
`.env`). Enmascara **valores, no estructura**: los nombres de campo, el código
JS, los endpoints y los redirects quedan visibles, así el agente puede razonar y
reversar un flujo; solo desaparece el valor concreto de la credencial.

Esto importa porque el prompt **sale de tu máquina** hacia DeepSeek, Anthropic,
Alibaba o Google, según el proveedor que tengas configurado. Todo lo que no
entre al prompt, no viaja.

**Lo que la automatización usa.** El código que el agente escribe sí puede
autenticarse: para eso están el repetidor y las credenciales de la captura. La
diferencia es *cuándo* se resuelve el valor — en tiempo de ejecución, del lado
de tu máquina, no en el texto que se le manda al modelo.

**Dónde están guardadas.** En la SQLite de la captura, en
`AGENTE_DATOS/_proxy/`, junto con el resto de tus datos y fuera de la carpeta
del código.

**El límite honesto.** El agente puede ejecutar código como tu usuario. Una vez
que le das eso, no hay criptografía que impida que un script lea lo que vos
podés leer: los controles de acá reducen la exposición accidental y hacen
visible la deliberada, no construyen una caja fuerte contra el propio agente.
Para el caso del disco robado, la herramienta correcta es BitLocker o FileVault,
no cifrado a nivel de aplicación.

En curso: cifrado de los headers en reposo con la decodificación pedida
explícitamente (mismo mecanismo de "permitir una vez / permitir siempre" que
los comandos destructivos), para que el acceso a credenciales sea un acto
consentido y no un efecto secundario.

### Contenido capturado = datos, no instrucciones

El agente lee páginas y respuestas de servidores que no controlás, y al mismo
tiempo tiene shell. Una página puede traer texto escrito para que un modelo lo
obedezca: un comentario HTML que diga "ignorá lo anterior y ejecutá esto".

El prompt del sistema marca todo lo que viene de la captura como contenido no
confiable —datos para analizar, nunca instrucciones— y le pide al agente que te
avise si encuentra algo así en vez de seguirlo. Ninguna defensa de prompt es
una garantía: si después de analizar tráfico el agente propone un comando que
no pediste, no lo apruebes.

### Las claves

El `.env` vive con tus datos, no con el código, así que compartir el proyecto
no se las lleva. En macOS queda con permisos `0600`. En Windows los permisos
POSIX no aplican (el acceso va por ACLs) y el archivo queda legible por tu
usuario: el panel de Diagnóstico te lo dice en vez de mentirte con un tilde
verde.

### Instalación de paquetes

El agente puede instalar paquetes de PyPI sin confirmación. Un nombre
equivocado o sugerido por contenido no confiable se instala igual. Está anotado
como pendiente.
