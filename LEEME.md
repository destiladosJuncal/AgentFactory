# AgenteDeepSeek — portable

Este paquete trae **solo el código**. No incluye claves ni conversaciones:
cuando lo abras, vas a configurar tus propias API keys desde la app.

## macOS

1. Abrí el `.dmg`.
2. Arrastrá **AgenteDeepSeek** (la ballena) a la carpeta Aplicaciones.
3. Doble clic para abrirla.

La **primera vez** se abre una Terminal mostrando la preparación del entorno:
baja Python (~24 MB) e instala las dependencias. Tarda menos de un minuto y
necesita internet. Cuando termina, la app abre sola. Los arranques siguientes
son directos y sin Terminal.

La primera vez macOS va a decir que no puede verificar al desarrollador. Es
esperable: la app no está firmada con un Apple Developer ID. Para abrirla:

  · Clic derecho sobre **AgenteDeepSeek.app** → **Abrir** → Abrir.
  · Si macOS no ofrece "Abrir": Ajustes del Sistema → Privacidad y
    seguridad → bajar hasta el aviso → "Abrir igualmente".

O desde la Terminal, una sola vez:

    xattr -dr com.apple.quarantine "<carpeta descomprimida>"

El primer arranque crea el entorno e instala las dependencias: tarda un minuto
y necesita internet. Los arranques siguientes son inmediatos.

## Requisitos

  · Mac con Apple Silicon (M1 o posterior)
  · Conexión a internet la primera vez

**No hace falta tener Python instalado.** Si la Mac no tiene uno usable, el
arrancador se baja uno (~24 MB) y lo deja adentro de esta misma carpeta. No
instala nada en el sistema ni pide contraseña: si borrás la carpeta, no queda
rastro.

Aviso: `/usr/bin/python3` que trae macOS **no es Python**, es un atajo que abre
el instalador de las herramientas de desarrollo de Apple. El arrancador lo
detecta y no lo usa.

## Tus datos

La app se instala sola en `~/AgenteDeepSeek/` la primera vez que la abrís, y
ahí quedan el intérprete de Python y las dependencias. Reemplazar la app por
una versión nueva no toca esa carpeta ni te obliga a bajar Python de nuevo.

Las conversaciones, proyectos y la biblioteca se guardan en:

    ~/tmp/agent_code/

Las API keys van en `~/tmp/agent_code/.env`, fuera de la carpeta de la app, así
que si volvés a compartir el zip no viajan con él.
