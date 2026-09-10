from core import plataforma
"""
Renderizador de Markdown para un widget Text de Tkinter.

Tkinter no sabe nada de Markdown: solo entiende "tags" aplicados a rangos de
texto. Este módulo parsea el Markdown línea por línea y va insertando con el
tag que corresponde, más dos cosas que no son texto:

  - Las tablas se alinean a mano (se miden las columnas y se rellena con
    espacios) y se pintan en monoespaciada, que es la única forma de que
    queden derechas en un Text.
  - Los bloques de código llevan una barrita con botones «Copiar» y
    «Ejecutar», insertados como widgets reales dentro del texto.

Soporta: # ## ### encabezados · **negrita** · *cursiva* · __subrayado__ ·
~~tachado~~ · `código` · ```bloques``` · tablas · listas · > citas · ---

Uso:

    configurar_tags(mi_text)
    render(mi_text, texto_markdown, al_ejecutar=mi_callback)
"""

import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from core import imagenes

# Lenguajes que sabemos ejecutar, y con qué motor.
LENGUAJES_EJECUTABLES = {
    "python": "python", "py": "python", "python3": "python",
    "bash": "shell", "sh": "shell", "shell": "shell", "zsh": "shell",
    "console": "shell", "terminal": "shell", "consola": "shell",
}


def configurar_tags(texto, fuente_ui=None, fuente_mono=None,
                    color_texto="#1c1e21", color_tenue="#7a808a",
                    color_acento="#1a56db", color_fondo_codigo="#f4f5f7"):
    """Define el aspecto de cada elemento. Se llama una vez por widget."""
    # Sin fuentes explícitas se usan las del sistema: las de macOS (Helvetica
    # Neue / Menlo) no existen en Windows y Tk las sustituye por cualquier cosa.
    if fuente_ui is None or fuente_mono is None:
        por_defecto_ui, por_defecto_mono = plataforma.fuentes()
        fuente_ui = fuente_ui or por_defecto_ui
        fuente_mono = fuente_mono or por_defecto_mono
    t = texto.tag_configure
    t("md_h1", font=(fuente_ui, 17, "bold"), foreground=color_texto,
      spacing1=12, spacing3=6)
    t("md_h2", font=(fuente_ui, 15, "bold"), foreground=color_texto,
      spacing1=10, spacing3=5)
    t("md_h3", font=(fuente_ui, 13, "bold"), foreground=color_texto,
      spacing1=8, spacing3=4)
    t("md_parrafo", font=(fuente_ui, 12), foreground=color_texto, spacing3=4)
    t("md_negrita", font=(fuente_ui, 12, "bold"))
    t("md_cursiva", font=(fuente_ui, 12, "italic"))
    t("md_subrayado", underline=True)
    t("md_tachado", overstrike=True)
    t("md_codigo_inline", font=(fuente_mono, 11), background=color_fondo_codigo)
    t("md_codigo", font=(fuente_mono, 11), background=color_fondo_codigo,
      lmargin1=16, lmargin2=16, spacing1=2, spacing3=2)
    t("md_lista", font=(fuente_ui, 12), foreground=color_texto,
      lmargin1=18, lmargin2=34, spacing3=3)
    t("md_cita", font=(fuente_ui, 12, "italic"), foreground=color_tenue,
      lmargin1=18, lmargin2=18)
    t("md_tabla", font=(fuente_mono, 11), foreground=color_texto)
    t("md_tabla_encabezado", font=(fuente_mono, 11, "bold"), foreground=color_texto)
    t("md_regla", foreground=color_tenue)
    t("md_link", foreground=color_acento, underline=True)
    t("md_abrible", foreground=color_acento, underline=True)


# --- Inline ----------------------------------------------------------------


# Rutas y URLs sueltas en medio del texto: se vuelven clickeables para no tener
# que copiarlas y hacer `open` a mano en la terminal.
_ABRIBLE = re.compile(
    r"(https?://[^\s<>\"')\]]+"          # http(s)://…
    r"|file://[^\s<>\"')\]]+"            # file:///Users/…
    r"|(?<![\w/])~?/[\w.\-/]*[\w.\-]"    # /Users/… o ~/Descargas/…
    r"(?:\.\w{1,6})?)")

# El orden importa: los delimitadores de dos caracteres van primero para que
# '**negrita**' no se lea como dos cursivas.
_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*", re.S), "md_negrita"),
    (re.compile(r"__(.+?)__", re.S), "md_subrayado"),
    (re.compile(r"~~(.+?)~~", re.S), "md_tachado"),
    (re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.S), "md_cursiva"),
    (re.compile(r"`([^`]+)`"), "md_codigo_inline"),
    (_ABRIBLE, "md_abrible"),
]
_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_IMAGEN = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")


def _partir_inline(linea: str, tag_base: str) -> List[Tuple[str, tuple]]:
    """Convierte una línea con marcas inline en [(texto, (tags,...)), ...]."""
    # Los links se aplanan primero a 'texto (url)' para no complicar el parseo.
    linea = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", linea)

    partes: List[Tuple[str, tuple]] = [(linea, (tag_base,))]
    for patron, tag in _INLINE:
        nuevas: List[Tuple[str, tuple]] = []
        for texto, tags in partes:
            # Un fragmento ya marcado no se vuelve a parsear.
            if len(tags) > 1:
                nuevas.append((texto, tags))
                continue
            ultimo = 0
            for m in patron.finditer(texto):
                if m.start() > ultimo:
                    nuevas.append((texto[ultimo:m.start()], tags))
                nuevas.append((m.group(1), tags + (tag,)))
                ultimo = m.end()
            if ultimo < len(texto):
                nuevas.append((texto[ultimo:], tags))
        partes = nuevas
    return [(t, tags) for t, tags in partes if t]


def _insertar_inline(texto, linea: str, tag_base: str, al_abrir=None):
    for fragmento, tags in _partir_inline(linea, tag_base):
        if "md_abrible" in tags and al_abrir is not None:
            _insertar_abrible(texto, fragmento, tags, al_abrir)
        else:
            texto.insert("end", fragmento, tags)


_contador_abribles = [0]


def _insertar_abrible(texto, fragmento: str, tags: tuple, al_abrir):
    """Inserta texto clickeable. Cada uno necesita su propio tag para poder
    llevar su destino: los binds de Tk son por tag, no por rango."""
    _contador_abribles[0] += 1
    etiqueta = f"abrir_{_contador_abribles[0]}"
    texto.insert("end", fragmento, tags + (etiqueta,))
    texto.tag_bind(etiqueta, "<Button-1>", lambda _e, d=fragmento: al_abrir(d))
    texto.tag_bind(etiqueta, "<Enter>", lambda _e: texto.configure(cursor="hand2"))
    texto.tag_bind(etiqueta, "<Leave>", lambda _e: texto.configure(cursor=""))


# --- Tablas ----------------------------------------------------------------

def _es_separador(linea: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", linea)) and "-" in linea


# Dentro de una tabla las marcas inline se sacan en vez de aplicarse: la
# alineación se calcula contando caracteres en monoespaciada, y una celda en
# negrita ocupa más ancho del que medimos, así que se desalinearía todo.
_MARCAS_INLINE = re.compile(r"\*\*|__|~~|(?<!\w)\*(?!\w)|`")


def _celdas(linea: str) -> List[str]:
    return [_MARCAS_INLINE.sub("", c).strip()
            for c in linea.strip().strip("|").split("|")]


ANCHO_TABLA_FALLBACK = 96      # caracteres, si no se puede medir el widget
MIN_COLUMNA = 6
MAX_COLUMNA_NATURAL = 46


def _ancho_visual(s: str) -> int:
    """Ancho en celdas de una fuente monoespaciada.

    Los emoji y los caracteres CJK ocupan DOS celdas. Contarlos como uno
    desalinea la tabla justo en las filas que los tienen — que suelen ser las
    de ⚠️ y ✅, o sea las que más se miran."""
    ancho = 0
    for c in s:
        if unicodedata.east_asian_width(c) in ("W", "F") or ord(c) >= 0x1F300:
            ancho += 2
        elif unicodedata.combining(c):
            ancho += 0
        else:
            ancho += 1
    return ancho


def _rellenar(s: str, ancho: int) -> str:
    return s + " " * max(0, ancho - _ancho_visual(s))


def _cortar_celda(texto_celda: str, ancho: int) -> List[str]:
    """Parte una celda en varias líneas sin exceder `ancho` visual."""
    if not texto_celda:
        return [""]
    lineas, actual = [], ""
    for palabra in texto_celda.split():
        tentativa = f"{actual} {palabra}".strip()
        if _ancho_visual(tentativa) <= ancho:
            actual = tentativa
            continue
        if actual:
            lineas.append(actual)
        # Una palabra sola más larga que la columna se parte a lo bruto.
        while _ancho_visual(palabra) > ancho:
            corte = ancho
            while corte > 1 and _ancho_visual(palabra[:corte]) > ancho:
                corte -= 1
            lineas.append(palabra[:corte])
            palabra = palabra[corte:]
        actual = palabra
    if actual:
        lineas.append(actual)
    return lineas or [""]


def _ancho_disponible(texto) -> int:
    """Cuántos caracteres monoespaciados entran a lo ancho del widget."""
    try:
        import tkinter.font as tkfont
        fuente = tkfont.Font(font=texto.tag_cget("md_tabla", "font") or texto.cget("font"))
        por_caracter = fuente.measure("0") or 8
        pixeles = texto.winfo_width()
        if pixeles <= 1:                       # todavía no se dibujó
            return ANCHO_TABLA_FALLBACK
        margen = 40                            # padding del Text + la sangría
        return max(40, int((pixeles - margen) / por_caracter))
    except Exception:
        return ANCHO_TABLA_FALLBACK


def _repartir_columnas(filas: List[List[str]], disponible: int) -> List[int]:
    """Asigna ancho a cada columna para que la tabla ENTRE en el ancho dado.

    Sin esto la tabla se dibuja con el ancho natural del contenido, supera el
    widget, y el ajuste de línea del Text la envuelve: ahí se pierde toda la
    alineación y hasta la línea de separación aparece cortada en pedazos.
    """
    columnas = len(filas[0])
    naturales = [
        min(MAX_COLUMNA_NATURAL, max(_ancho_visual(f[i]) for f in filas))
        for i in range(columnas)
    ]
    separadores = 2 * (columnas - 1) + 2
    sobrante = disponible - separadores

    if sum(naturales) <= sobrante:
        return naturales

    # No entra: se le saca SIEMPRE a la columna más ancha, de a un carácter.
    # Repartir proporcionalmente sería más simple pero castiga a las columnas
    # cortas — una de 14 caracteres quedaría en 8 y partiría palabras al medio
    # ("Automati/zación") mientras la de al lado sigue holgada. Achicando la
    # mayor, las angostas conservan su ancho natural mientras se pueda.
    anchos = list(naturales)
    while sum(anchos) > sobrante:
        i = anchos.index(max(anchos))
        if anchos[i] <= MIN_COLUMNA:
            break
        anchos[i] -= 1
    return anchos


def _render_tabla(texto, filas: List[List[str]]):
    """Dibuja la tabla como grilla de caracteres, envolviendo dentro de cada
    celda para que la fila nunca supere el ancho del widget."""
    columnas = max(len(f) for f in filas)
    filas = [f + [""] * (columnas - len(f)) for f in filas]
    anchos = _repartir_columnas(filas, _ancho_disponible(texto))

    def emitir(celdas, tag):
        # Cada celda puede ocupar varias líneas; la fila mide lo que la más alta.
        partidas = [_cortar_celda(c, anchos[i]) for i, c in enumerate(celdas)]
        alto = max(len(p) for p in partidas)
        for n in range(alto):
            linea = "  ".join(
                _rellenar(partidas[i][n] if n < len(partidas[i]) else "", anchos[i])
                for i in range(columnas))
            texto.insert("end", "  " + linea.rstrip() + "\n", tag)

    emitir(filas[0], "md_tabla_encabezado")
    texto.insert("end", "  " + "  ".join("─" * a for a in anchos) + "\n", "md_regla")
    for fila in filas[1:]:
        emitir(fila, "md_tabla")
    texto.insert("end", "\n")


# --- Bloques de código -----------------------------------------------------

def _barra_de_codigo(texto, codigo: str, lenguaje: str,
                     al_ejecutar: Optional[Callable[[str, str], None]]):
    """Inserta la fila de botones que acompaña a un bloque de código."""
    import tkinter as tk
    from tkinter import ttk

    barra = tk.Frame(texto, bg="#f4f5f7")
    etiqueta = lenguaje or "texto"
    tk.Label(barra, text=f"  {etiqueta}", bg="#f4f5f7", fg="#7a808a",
             font=(plataforma.fuentes()[0], 10)).pack(side="left")

    def copiar():
        texto.clipboard_clear()
        texto.clipboard_append(codigo)
        boton_copiar.configure(text="✓ Copiado")
        barra.after(1200, lambda: boton_copiar.configure(text="Copiar"))

    boton_copiar = ttk.Button(barra, text="Copiar", width=9, command=copiar)
    boton_copiar.pack(side="left", padx=6, pady=2)

    motor = LENGUAJES_EJECUTABLES.get((lenguaje or "").lower())
    if motor and al_ejecutar is not None:
        ttk.Button(barra, text="▶ Ejecutar", width=11,
                   command=lambda: al_ejecutar(codigo, motor)).pack(side="left", pady=2)

    texto.window_create("end", window=barra)
    texto.insert("end", "\n")


def _insertar_imagen(texto, ruta: str, alt: str, base_imagenes, al_abrir_imagen, al_abrir=None):
    """Inserta la imagen en el Text, o un aviso si no se puede."""
    resultado = imagenes.cargar(ruta, base_imagenes)

    if "error" in resultado:
        texto.insert("end", f"🖼  {alt or ruta}\n", "md_parrafo")
        texto.insert("end", f"    {resultado['error']}\n", "md_cita")
        # Aunque no se pueda mostrar, ofrecemos abrir la carpeta que la
        # contendría: suele ser lo que uno quiere hacer a continuación.
        from core.plataforma import limpiar_ruta
        carpeta = Path(limpiar_ruta(str(ruta))).parent
        if al_abrir is not None and carpeta.is_dir():
            texto.insert("end", "    ", "md_cita")
            _insertar_abrible(texto, str(carpeta), ("md_abrible",), al_abrir)
            texto.insert("end", "\n")
        return

    # Tk descarta la imagen si nadie la referencia: se guarda en el propio
    # widget, que vive tanto como la ventana.
    if not hasattr(texto, "_imagenes_retenidas"):
        texto._imagenes_retenidas = []
    texto._imagenes_retenidas.append(resultado["imagen"])

    texto.image_create("end", image=resultado["imagen"])
    texto.insert("end", "\n")

    archivo = resultado["ruta"]
    orig = resultado["original"]
    escala = ""
    if (orig[0], orig[1]) != (resultado["ancho"], resultado["alto"]):
        escala = f" · mostrada a {resultado['ancho']}×{resultado['alto']}"
    pie = f"{alt + '  ·  ' if alt else ''}{archivo.name}  ({orig[0]}×{orig[1]}{escala})"
    texto.insert("end", f"   {pie}\n", "md_cita")

    if al_abrir_imagen is not None:
        etiqueta = "md_abrir_" + re.sub(r"\W+", "_", str(archivo))[-40:]
        inicio = texto.index("end-2l linestart")
        texto.insert("end", "   Abrir en el visor del sistema\n", ("md_link", etiqueta))
        texto.tag_bind(etiqueta, "<Button-1>", lambda _e, a=archivo: al_abrir_imagen(a))
        texto.tag_bind(etiqueta, "<Enter>", lambda _e: texto.configure(cursor="hand2"))
        texto.tag_bind(etiqueta, "<Leave>", lambda _e: texto.configure(cursor=""))
    texto.insert("end", "\n")


# --- Render principal ------------------------------------------------------

def render(texto, markdown: str,
           al_ejecutar: Optional[Callable[[str, str], None]] = None,
           base_imagenes: Optional[Path] = None,
           al_abrir_imagen: Optional[Callable[[Path], None]] = None,
           al_abrir: Optional[Callable[[str], None]] = None):
    """Inserta `markdown` renderizado al final del widget.

    `al_ejecutar(codigo, motor)` se llama cuando tocás ▶ Ejecutar; `motor` es
    'python' o 'shell'. Si es None, no aparece el botón."""
    lineas = (markdown or "").split("\n")
    i = 0
    while i < len(lineas):
        linea = lineas[i]

        # Bloque de código ```
        cerca = re.match(r"^\s*```(\w*)\s*$", linea)
        if cerca:
            lenguaje = cerca.group(1)
            cuerpo = []
            i += 1
            while i < len(lineas) and not re.match(r"^\s*```\s*$", lineas[i]):
                cuerpo.append(lineas[i])
                i += 1
            i += 1
            codigo = "\n".join(cuerpo)
            _barra_de_codigo(texto, codigo, lenguaje, al_ejecutar)
            texto.insert("end", codigo + "\n", "md_codigo")
            texto.insert("end", "\n")
            continue

        # Imagen en su propia línea: ![alt](ruta)
        imagen = _IMAGEN.match(linea)
        if imagen:
            _insertar_imagen(texto, imagen.group(2), imagen.group(1),
                             base_imagenes, al_abrir_imagen, al_abrir)
            i += 1
            continue

        # Tabla: una fila con | seguida de un separador
        if "|" in linea and i + 1 < len(lineas) and _es_separador(lineas[i + 1]):
            filas = [_celdas(linea)]
            i += 2
            while i < len(lineas) and "|" in lineas[i] and lineas[i].strip():
                filas.append(_celdas(lineas[i]))
                i += 1
            _render_tabla(texto, filas)
            continue

        # Encabezados
        encabezado = re.match(r"^(#{1,3})\s+(.*)$", linea)
        if encabezado:
            nivel = len(encabezado.group(1))
            _insertar_inline(texto, encabezado.group(2), f"md_h{nivel}", al_abrir)
            texto.insert("end", "\n")
            i += 1
            continue

        # Regla horizontal
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", linea):
            texto.insert("end", "─" * 60 + "\n", "md_regla")
            i += 1
            continue

        # Cita
        cita = re.match(r"^\s*>\s?(.*)$", linea)
        if cita:
            _insertar_inline(texto, cita.group(1), "md_cita", al_abrir)
            texto.insert("end", "\n")
            i += 1
            continue

        # Listas (con viñeta o numeradas)
        lista = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", linea)
        if lista:
            sangria = "   " * (len(lista.group(1)) // 2)
            marca = "•" if lista.group(2) in "-*+" else lista.group(2)
            texto.insert("end", f"{sangria}{marca} ", "md_lista")
            _insertar_inline(texto, lista.group(3), "md_lista", al_abrir)
            texto.insert("end", "\n")
            i += 1
            continue

        # Línea vacía o párrafo normal
        if not linea.strip():
            texto.insert("end", "\n")
        else:
            _insertar_inline(texto, linea, "md_parrafo", al_abrir)
            texto.insert("end", "\n")
        i += 1
