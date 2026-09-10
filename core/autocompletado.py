"""
Autocompletado de sitios capturados en el cuadro de escritura.

## Por qué existe

El agente tiene que trabajar sobre un dominio concreto, y la forma en que se lo
nombraba era escribiéndolo a mano: "sacame los trabajos de linkedin". Eso deja
que el modelo adivine a qué host se refiere, y adivinar mal no se nota — sale
una extracción con datos de otro dominio y parece correcta.

Con esto, escribís `@` y aparece la lista de lo que REALMENTE capturaste. Elegís
y en el prompt queda el dominio exacto. La ambigüedad se resuelve del lado de la
persona, que es la única que sabe a qué se refería, y antes de que salga el
mensaje.

Es la contraparte de core/dominios.py: ahí el código se niega a adivinar cuando
el nombre es ambiguo; acá se evita que la ambigüedad exista.

## Cómo se usa

    completador = CompletadorSitios(widget_texto, proveedor=lista_de_sitios)

`proveedor` es una función sin argumentos que devuelve
[{"sitio": "linkedin.com", "hosts": [...], "flujos": 12}, ...] — la firma de
proxy_tool.listar_sitios_capturados()["sitios"]. Se llama al abrir la lista, no
al construir: la captura cambia mientras la app está abierta.

Quien lo usa tiene que delegarle las teclas de navegación, porque compiten con
las del cuadro de texto (Enter manda el mensaje). Los métodos `al_enter`,
`al_flecha` y `al_escape` devuelven True si consumieron la tecla.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

# Cuántas opciones se muestran de una. Más que esto no se lee: se sigue
# escribiendo para filtrar.
MAX_VISIBLES = 8

DISPARADOR = "@"


class CompletadorSitios:
    def __init__(self, texto: tk.Text, proveedor: Callable[[], List[dict]],
                 fuente=None):
        self.texto = texto
        self.proveedor = proveedor
        self.fuente = fuente
        self.popup: Optional[tk.Toplevel] = None
        self.lista: Optional[tk.Listbox] = None
        self.opciones: List[str] = []
        self._inicio: Optional[str] = None   # índice Tk donde arranca el '@'

        texto.bind("<KeyRelease>", self._al_soltar_tecla, add="+")
        # Si el foco se va, la lista no puede quedar flotando sobre la ventana.
        texto.bind("<FocusOut>", lambda _e: self.cerrar(), add="+")

    # --- API para quien lo integra -----------------------------------------

    @property
    def abierto(self) -> bool:
        return self.popup is not None

    def al_enter(self) -> bool:
        """True si había una opción marcada y se insertó."""
        if not self.abierto:
            return False
        self._aceptar()
        return True

    def al_flecha(self, delta: int) -> bool:
        if not self.abierto or self.lista is None:
            return False
        total = self.lista.size()
        if not total:
            return False
        actual = self.lista.curselection()
        i = (actual[0] if actual else 0) + delta
        i = max(0, min(total - 1, i))
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(i)
        self.lista.activate(i)
        self.lista.see(i)
        return True

    def al_escape(self) -> bool:
        if not self.abierto:
            return False
        self.cerrar()
        return True

    def cerrar(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
        self.popup, self.lista, self._inicio = None, None, None
        self.opciones = []

    # --- Detección del prefijo ---------------------------------------------

    def _prefijo_en_cursor(self):
        """(indice_del_arroba, texto_escrito_despues) o (None, None).

        Se mira solo la línea del cursor y hacia atrás hasta el último '@'. Si
        en el medio hay un espacio, no es una mención: es un '@' viejo de más
        arriba y lo que se está escribiendo es otra cosa."""
        try:
            linea, col = map(int, self.texto.index("insert").split("."))
        except (tk.TclError, ValueError):
            return None, None
        if col == 0:
            return None, None
        principio = self.texto.get(f"{linea}.0", "insert")
        pos = principio.rfind(DISPARADOR)
        if pos == -1:
            return None, None
        escrito = principio[pos + 1:]
        if any(c.isspace() for c in escrito):
            return None, None
        return f"{linea}.{pos}", escrito

    # --- Ciclo de vida ------------------------------------------------------

    def _al_soltar_tecla(self, evento):
        # Las teclas de navegación las maneja quien integra, no acá: si se
        # reaccionara al KeyRelease de las flechas, se recalcularía la lista
        # justo después de que el usuario movió la selección.
        if evento.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        inicio, escrito = self._prefijo_en_cursor()
        if inicio is None:
            self.cerrar()
            return
        self._inicio = inicio
        self._refrescar(escrito)

    def _candidatos(self, escrito: str) -> List[str]:
        try:
            sitios = self.proveedor() or []
        except Exception:
            return []

        etiquetas, vistos = [], set()
        filtro = (escrito or "").lower()
        for entrada in sitios:
            dominio = entrada.get("sitio") or ""
            hosts = entrada.get("hosts") or []
            flujos = entrada.get("flujos") or 0
            # El dominio primero; los hosts después, y solo si aportan algo
            # distinto (no repetir 'linkedin.com' como host de sí mismo).
            candidatos = [dominio] + [h for h in sorted(hosts) if h != dominio]
            for c in candidatos:
                if not c or c in vistos or filtro not in c.lower():
                    continue
                vistos.add(c)
                if c == dominio:
                    etiquetas.append((c, f"{c}   ({flujos} flujos)"))
                else:
                    # A cada host se le muestra a qué dominio pertenece. Suena
                    # redundante con 'www.linkedin.com → linkedin.com', pero es
                    # lo que evita el engaño en el caso que importa: filtrando
                    # por "google" aparece 'google.com.ar.phish.net', y sin esta
                    # aclaración parece de Google cuando es de phish.net.
                    etiquetas.append((c, f"   {c}  ·  {dominio}"))
        self.opciones = [valor for valor, _ in etiquetas]
        return [texto for _, texto in etiquetas]

    def _refrescar(self, escrito: str):
        visibles = self._candidatos(escrito)
        if not visibles:
            self.cerrar()
            return
        if self.popup is None:
            self._abrir()
        if self.lista is None:
            return
        self.lista.delete(0, "end")
        mostradas = visibles[:MAX_VISIBLES * 4]
        for etiqueta in mostradas:
            self.lista.insert("end", etiqueta)
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(0)
        self.lista.activate(0)
        # El ancho se ajusta al texto más largo. Sin esto la lista se quedaba en
        # su ancho por defecto y CORTABA las etiquetas justo donde dicen a qué
        # dominio pertenece cada host — que es la parte que evita confundir
        # 'google.com.ar.phish.net' con Google.
        self.lista.configure(
            height=min(MAX_VISIBLES, self.lista.size()),
            width=min(70, max(28, max((len(e) for e in mostradas), default=28) + 2)))
        self._ubicar()

    def _abrir(self):
        self.popup = tk.Toplevel(self.texto)
        self.popup.overrideredirect(True)     # sin barra de título
        try:
            self.popup.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        marco = ttk.Frame(self.popup, relief="solid", borderwidth=1)
        marco.pack(fill="both", expand=True)
        self.lista = tk.Listbox(marco, activestyle="none", exportselection=False,
                                highlightthickness=0, borderwidth=0,
                                font=self.fuente or None)
        self.lista.pack(fill="both", expand=True)
        self.lista.bind("<Double-Button-1>", lambda _e: self._aceptar())
        self.lista.bind("<Return>", lambda _e: self._aceptar())

    def _ubicar(self):
        """Debajo del cursor. Si no cabe abajo, arriba."""
        if self.popup is None:
            return
        try:
            caja = self.texto.bbox("insert")
        except tk.TclError:
            caja = None
        if caja:
            x, y, _, alto_linea = caja
        else:
            # bbox() no devuelve nada si el widget todavía no está mapeado o si
            # el cursor quedó fuera de la parte visible. Antes acá se cerraba la
            # lista, que es peor: la opción desaparecía justo cuando el cuadro
            # estaba scrolleado. Se cae a la esquina del cuadro y sigue.
            x, y, alto_linea = 0, 0, 0
        px = self.texto.winfo_rootx() + x
        py = self.texto.winfo_rooty() + y + alto_linea + 2

        self.popup.update_idletasks()
        alto = self.popup.winfo_reqheight()
        ancho = max(self.popup.winfo_reqwidth(), 260)
        if py + alto > self.texto.winfo_screenheight():
            py = self.texto.winfo_rooty() + y - alto - 2
        self.popup.geometry(f"{ancho}x{alto}+{px}+{py}")

    def _aceptar(self):
        if self.lista is None or self._inicio is None:
            self.cerrar()
            return
        seleccion = self.lista.curselection()
        indice = seleccion[0] if seleccion else 0
        if indice >= len(self.opciones):
            self.cerrar()
            return
        elegido = self.opciones[indice]
        inicio = self._inicio
        self.cerrar()
        # Reemplaza '@loquesea' por el dominio exacto. Se deja un espacio para
        # poder seguir escribiendo la frase sin pegarse a la palabra siguiente.
        try:
            self.texto.delete(inicio, "insert")
            self.texto.insert(inicio, elegido + " ")
            self.texto.see("insert")
        except tk.TclError:
            pass
