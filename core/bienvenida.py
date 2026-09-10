"""
Pantalla de arranque: el logo de AgentFactory en ASCII y el mensaje inicial.

El arte se generó desde el mismo PNG del icono, reduciéndolo a una grilla de
caracteres y midiendo cuánto VERDE tiene cada celda respecto del rojo y el azul
—no el brillo—, porque el fondo de lluvia Matrix también es verde oscuro y por
brillo la silueta se perdía entre los caracteres del fondo.

Queda embebido como texto y no se recalcula: así el arranque no depende de que
el PNG siga estando ni de que Pillow esté instalado.
"""

from typing import List, Optional

LOGO = r"""

                          =+==+=
                .--      .oooooo.      --.
             .+**oo*--=++********++=--*ooo*+.
              =o****ooooo***++***oooooo***o=
             .=**oo*+=-:.        .:-=+*oo**=.
       --:..+ooo*+-.                  .-+oooo+:.:=-
     .+ooo*****=.                        .=*ooooooo+.
     =o*****o+.             =*             .+o*o**oo=
       -***o=              :oo+              =o*oo=
       +o*o=         .:   .*o*o=   -:         =o*o+
      :o***          =+=: +****o: =o*          *o*o-
  .++****o=          +++++********ooo.         =o*oo**+.
  :ooo***o-         :++++*******ooooo=         -ooooooo:
  .++****o=         =+=+**********o*o*         =o*oo**+.
      :o*o*        .===+++++********oo.        *o*o-
       +o*o=        .:=+++++********=.        =ooo+
       =o**o=           :=+******=:          =oooo=
     =oo**o*o+.            :=+=:           .+ooooooo=
     .+ooooooo*=.                        .=*ooooooo+.
       -=:.:+oooo+:.                  .:+oooo+:.:=-
             .=*ooo*+=:..        ..:=+*oooo+:
              =o***oooooo********oooooo**oo=
             .+*ooo*--=+**oooooo**+=--*ooo*+.
                .-=      .oooooo.      =-.
                          =++++=
"""


def titulo_desde_texto(texto: str, maximo: int = 45) -> str:
    """Saca un título de conversación del primer mensaje.

    Se queda con la primera línea con contenido, sin signos de pregunta ni
    puntuación final, y cortando en un espacio para no partir una palabra."""
    lineas = [l.strip() for l in (texto or "").splitlines() if l.strip()]
    if not lineas:
        return ""
    titulo = lineas[0].strip("¿?¡!.,;: \t")
    if len(titulo) > maximo:
        corte = titulo.rfind(" ", 0, maximo)
        titulo = titulo[:corte if corte > maximo // 2 else maximo].rstrip() + "…"
    return titulo


def texto_inicial(hay_credenciales: bool, proveedor: str,
                  n_conversaciones: int, ruta_env) -> List[tuple]:
    """Devuelve [(texto, tag)] para pintar en la transcripción al abrir.

    Separa dos situaciones que necesitan mensajes muy distintos: la primera vez
    (sin API key, hay que configurar antes de poder hacer nada) y el uso normal.
    """
    bloques: List[tuple] = [(LOGO.strip("\n"), "arte"), ("\n\n", "cuerpo")]

    if not hay_credenciales:
        bloques += [
            ("Falta configurar una API key para poder empezar.\n\n", "titulo_aviso"),
            ("Andá a la pestaña  ⚙️ Configuración  y cargá la clave de DeepSeek "
             "o la de Claude. Después tocá «Probar» para confirmar que la API "
             "la acepta: construir el cliente no alcanza, hay que hablar con el "
             "servidor.\n\n", "aviso"),
            (f"Se guardan en {ruta_env}, junto a tus datos y fuera de la carpeta "
             f"de la app — así compartir el programa nunca se lleva tus claves.\n",
             "aviso"),
        ]
        return bloques

    if n_conversaciones == 0:
        bloques += [
            (f"Todo listo · {proveedor}\n\n", "titulo_aviso"),
            ("Escribí abajo y arranco una conversación nueva sola.\n", "aviso"),
        ]
    else:
        bloques += [
            (f"{proveedor}\n\n", "titulo_aviso"),
            ("Elegí una conversación de la izquierda, o escribí abajo y te creo "
             "una nueva.\n", "aviso"),
        ]
    return bloques
