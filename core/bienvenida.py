"""
Startup screen: the AgentFactory logo in ASCII and the initial message.

The art was generated from the icon's own PNG, reducing it to a grid of
characters and measuring how much GREEN each cell has relative to red and blue
—not the brightness—, because the Matrix-rain background is also dark green and
by brightness the silhouette got lost among the background characters.

It's embedded as text and not recomputed: that way startup doesn't depend on the
PNG still being there nor on Pillow being installed.

(The user-facing message text and the styling tags below are still Spanish on
purpose: they move to the i18n layer in a later phase, not this rename.)
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


def title_from_text(text: str, maximum: int = 45) -> str:
    """Derives a conversation title from the first message.

    Keeps the first line with content, without question marks or trailing
    punctuation, and cutting at a space so as not to split a word."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return ""
    title = lines[0].strip("¿?¡!.,;: \t")
    if len(title) > maximum:
        cut = title.rfind(" ", 0, maximum)
        title = title[:cut if cut > maximum // 2 else maximum].rstrip() + "…"
    return title


def initial_text(hay_credenciales: bool, proveedor: str,
                  n_conversaciones: int, ruta_env) -> List[tuple]:
    """Returns [(text, tag)] to paint in the transcript on open.

    Separates two situations that need very different messages: the first time
    (no API key, you have to configure before doing anything) and normal use.
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
