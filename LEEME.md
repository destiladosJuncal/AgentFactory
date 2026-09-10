# AgentFactory - portable

Este paquete trae **solo el codigo**. No incluye claves ni conversaciones:
cuando lo abras, vas a configurar tus propias API keys desde la app.

**No hace falta tener Python instalado.** Si esta computadora no tiene uno
usable, el arrancador se baja uno y lo deja adentro de esta misma carpeta. No
instala nada en el sistema ni pide contrasena de administrador: si borras la
carpeta, no queda rastro.

## Windows 10 / 11

1. Descomprimi el zip **entero** en una carpeta tuya
   (por ejemplo `C:\Users\<vos>\AgentFactory`).
2. Doble clic en **INICIAR.bat**.

La primera vez se abre una ventana negra mostrando la preparacion: baja Python
(~30 MB) e instala las dependencias. Tarda un par de minutos y necesita
internet. Cuando termina, la app abre sola y la ventana negra se cierra. Los
arranques siguientes son directos.

Dos avisos esperables la primera vez:

  - **"Windows protegio tu PC"** (SmartScreen). Es porque el archivo no esta
    firmado. Clic en *Mas informacion* -> *Ejecutar de todas formas*.
  - Si descomprimiste con el Explorador y el `.bat` no arranca, hace clic
    derecho sobre el -> *Propiedades* -> marca **Desbloquear** -> Aceptar.

Importante: descomprimi **antes** de ejecutar. Si haces doble clic en el `.bat`
desde adentro del zip, Windows lo corre en una carpeta temporal y la
instalacion se pierde.

El `python.exe` que Windows trae en el PATH **no es Python**: es un atajo que
abre la Microsoft Store. El arrancador lo detecta y no lo usa.

Para ver el detalle de un arranque que falla, corre `INICIAR.bat` desde una
consola, o mira el log en `%USERPROFILE%\tmp\agentfactory\_logs\ui.log`.

## macOS

1. Descomprimi el zip.
2. Doble clic en **INICIAR.command**.

La primera vez macOS va a decir que no puede verificar al desarrollador: la app
no esta firmada con un Apple Developer ID. Para abrirla:

  - Clic derecho sobre **INICIAR.command** -> **Abrir** -> Abrir.
  - Si macOS no ofrece "Abrir": Ajustes del Sistema -> Privacidad y
    seguridad -> bajar hasta el aviso -> "Abrir igualmente".

O desde la Terminal, una sola vez:

    xattr -dr com.apple.quarantine "<carpeta descomprimida>"

Aviso: `/usr/bin/python3` que trae macOS **no es Python**, es un atajo que abre
el instalador de las herramientas de desarrollo de Apple. El arrancador lo
detecta y no lo usa.

## Requisitos

  - Windows 10 (1803 o posterior) / 11 de 64 bits, o Mac con Apple Silicon
  - Conexion a internet la primera vez

## Tus datos

Las conversaciones, tareas y la biblioteca se guardan **fuera** de esta
carpeta:

    Windows   %USERPROFILE%\tmp\agentfactory\
    macOS     ~/tmp/agentfactory/

Las API keys van en el `.env` de esa carpeta, no en la del codigo, asi que si
volves a compartir el zip no viajan con el. Reemplazar esta carpeta por una
version nueva de la app no toca tus datos.
