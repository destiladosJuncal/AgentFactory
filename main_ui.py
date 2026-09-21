#!/usr/bin/env python3
"""
UI de escritorio para AgenteDeepSeek (Tkinter, sin dependencias nuevas).

Es una cáscara sobre lo que ya existe en consola, no una reimplementación:

  💬 Conversaciones  -> core/conversaciones.py + core/chat.py   (main_chat.py)
  🔁 Proyectos       -> core/proyectos.py + core/agente_interactivo.py
                        (main_interactivo.py)
  📚 Biblioteca      -> core/biblioteca.py

Se puede correr directo:

    /Applications/AgenteDeepSeek/venv/bin/python main_ui.py

o desde /Applications/AgentFactory.app (ver instalar_ui.sh).
"""

import os
import queue
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# Drag & drop nativo. Si tkinterdnd2 no está, la app arranca igual y queda el
# botón «📎 Adjuntar» como alternativa.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    BASE_TK = TkinterDnD.Tk
    HAY_DND = True
except ImportError:  # pragma: no cover
    BASE_TK = tk.Tk
    DND_FILES = None
    HAY_DND = False

# ---------------------------------------------------------------------------
# Localizar la instalación (core/, .env, venv). Sirve tanto si este archivo
# vive adentro del directorio instalado como si vive en el .app.
# ---------------------------------------------------------------------------

# Orden de búsqueda. La instalación en el HOME va PRIMERO a propósito: es la
# que el usuario puede actualizar y restaurar (⌘0) sin sudo. La de /Applications
# es de root y queda como respaldo de instalaciones viejas.
# AgentFactory es un fork independiente: resuelve SOLO a su propia carpeta.
# No se cae nunca a una instalación de AgenteDeepSeek — es otra app distinta.
CANDIDATOS_INSTALACION = [
    Path(__file__).resolve().parent,
]


def _resolver_instalacion() -> Path:
    # Empaquetado con PyInstaller, los módulos viven DENTRO del ejecutable: no
    # hay ningún core/chat.py en disco que encontrar, y la comprobación de
    # abajo fallaría siempre. La carpeta de la app es la del .exe, que es
    # además donde quedan los datos que se empaquetaron al lado.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    for candidato in CANDIDATOS_INSTALACION:
        if (candidato / "core" / "chat.py").exists():
            return candidato
    raiz = tk.Tk()
    raiz.withdraw()
    messagebox.showerror(
        "AgentFactory",
        "No encontré la instalación del agente.\n\n"
        "Busqué en:\n  " + "\n  ".join(str(c) for c in CANDIDATOS_INSTALACION) +
        "\n\nInstalá el bundle (instalar_interactivo.sh) y volvé a abrir."
    )
    sys.exit(1)


APP_DIR = _resolver_instalacion()
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

from dotenv import load_dotenv  # noqa: E402

# El .env vive con los datos, no con el código (ver core/rutas.py).
from core.rutas import cargar_env  # noqa: E402
RUTA_ENV = cargar_env()

from core.conversaciones import GestorConversaciones, CONVERSACIONES_DIR  # noqa: E402
from core.chat import ConversacionChat  # noqa: E402
from core.proyectos import GestorProyectos, AGENT_CODE_DIR  # noqa: E402
from core.agente_interactivo import AgenteInteractivo, OBJETIVOS_DEFAULT  # noqa: E402
from core.biblioteca import Biblioteca  # noqa: E402
from core.proveedores import crear_proveedor, proveedor_configurado  # noqa: E402
from core.versiones import GestorVersiones  # noqa: E402
from core.procesador import procesar, ErrorProcesador  # noqa: E402
from core import ejecucion  # noqa: E402
from core import render_markdown  # noqa: E402
from core import plataforma, rutas  # noqa: E402
from core import formato  # noqa: E402
from core import autocompletado  # noqa: E402
from core import bienvenida  # noqa: E402
from core import proxy as proxymod  # noqa: E402
from core import mac_icono  # noqa: E402
from core import config as configuracion, empaquetar  # noqa: E402
from core.proveedores import MODELOS_DISPONIBLES, etiqueta_de, desde_etiqueta  # noqa: E402

from core import interprete

from core import consola  # noqa: E402
consola.setup_utf8()


# Si este archivo corre desde adentro del .app, su carpeta es el Resources del
# bundle — y ese main_ui.py también entra en el historial de versiones.
_AQUI = Path(__file__).resolve().parent
RECURSOS_APP = _AQUI if (_AQUI.name == "Resources" and ".app" in str(_AQUI)) else None

# ---------------------------------------------------------------------------
# Estilo
# ---------------------------------------------------------------------------

# Fuentes según el sistema: en Windows no existen Helvetica Neue ni Menlo.
FUENTE_UI, FUENTE_MONO = plataforma.fuentes()

COLOR_FONDO = "#ffffff"
COLOR_PANEL = "#f4f5f7"
COLOR_TEXTO = "#1c1e21"
COLOR_TENUE = "#7a808a"
COLOR_USUARIO = "#1a56db"
COLOR_AGENTE = "#0f7b4f"
COLOR_ERROR = "#b42318"


_mover_a_papelera = plataforma.mover_a_papelera


def _tamano_legible(ruta: Path) -> str:
    total = sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())
    return formato.size(total)


class ColaSalida:
    """Redirige print() de los módulos del agente a una cola, para poder
    mostrar la actividad (tool calls, iteraciones) dentro de la UI sin que
    los hilos toquen widgets directamente."""

    def __init__(self, cola: queue.Queue, original):
        self.cola = cola
        self.original = original
        self._buffer = ""

    def write(self, texto: str):
        if self.original is not None:
            try:
                self.original.write(texto)
            except Exception:
                pass
        self._buffer += texto
        while "\n" in self._buffer:
            linea, self._buffer = self._buffer.split("\n", 1)
            self.cola.put(("log", linea))

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass


class DialogoNuevoProyecto(tk.Toplevel):
    """Pide objetivo + umbral + máximo de iteraciones, igual que el prompt
    de main_interactivo.py pero en un formulario."""

    def __init__(self, padre):
        super().__init__(padre)
        self.title("Nuevo proyecto")
        self.resultado = None
        self.transient(padre)
        self.resizable(False, False)
        self.configure(bg=COLOR_PANEL, padx=18, pady=16)

        ttk.Label(self, text="¿Qué querés que construya el agente?",
                  font=(FUENTE_UI, 13, "bold")).pack(anchor="w")
        ttk.Label(self, text="Se va a iterar generar → ejecutar → evaluar hasta llegar al umbral.",
                  foreground=COLOR_TENUE).pack(anchor="w", pady=(2, 8))

        self.objetivo = tk.Text(self, height=5, width=62, wrap="word",
                                font=(FUENTE_UI, 12), relief="solid", borderwidth=1)
        self.objetivo.pack(fill="x")
        self.objetivo.focus_set()

        fila = ttk.Frame(self)
        fila.pack(fill="x", pady=(12, 0))

        ttk.Label(fila, text="Umbral de éxito").grid(row=0, column=0, sticky="w")
        self.umbral = tk.StringVar(value="0.75")
        ttk.Spinbox(fila, from_=0.1, to=1.0, increment=0.05, width=6,
                    textvariable=self.umbral).grid(row=0, column=1, padx=(8, 24))

        ttk.Label(fila, text="Máx. iteraciones").grid(row=0, column=2, sticky="w")
        self.max_iter = tk.StringVar(value="15")
        ttk.Spinbox(fila, from_=1, to=200, width=6,
                    textvariable=self.max_iter).grid(row=0, column=3, padx=(8, 0))

        botones = ttk.Frame(self)
        botones.pack(fill="x", pady=(16, 0))
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(botones, text="Crear", command=self._aceptar).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def _aceptar(self):
        descripcion = self.objetivo.get("1.0", "end").strip()
        if not descripcion:
            messagebox.showwarning("Nuevo proyecto", "El objetivo no puede estar vacío.", parent=self)
            return
        try:
            umbral = float(self.umbral.get())
            max_iter = int(self.max_iter.get())
        except ValueError:
            messagebox.showwarning("Nuevo proyecto", "Umbral o iteraciones inválidos.", parent=self)
            return
        self.resultado = (descripcion, umbral, max_iter)
        self.destroy()


class DialogoConfirmacion(tk.Toplevel):
    """Freno para operaciones destructivas.

    El agente puede correr shell y Python sin restricciones, pero cuando lo que
    va a correr borra o sobrescribe algo, el hilo de trabajo queda esperando acá
    hasta que decidas. 'Permitir siempre' vale por tipo de operación y solo
    hasta que cierres la app."""

    def __init__(self, padre, resumen: str, detalle: str, clave: str):
        super().__init__(padre)
        self.decision = "no"
        self.title("Confirmar operación")
        self.transient(padre)
        self.configure(bg=COLOR_PANEL, padx=18, pady=16)
        self.resizable(False, False)

        tk.Label(self, text="⚠️  El agente quiere hacer algo que no se puede deshacer",
                 bg=COLOR_PANEL, fg=COLOR_ERROR,
                 font=(FUENTE_UI, 13, "bold")).pack(anchor="w")
        tk.Label(self, text=resumen, bg=COLOR_PANEL, fg=COLOR_TEXTO,
                 font=(FUENTE_UI, 12), wraplength=600, justify="left").pack(anchor="w", pady=(4, 10))

        tk.Label(self, text="Esto es lo que va a ejecutar:", bg=COLOR_PANEL,
                 fg=COLOR_TENUE, font=(FUENTE_UI, 11)).pack(anchor="w")
        caja = tk.Text(self, height=min(12, max(3, detalle.count("\n") + 2)), width=76,
                       wrap="word", font=(FUENTE_MONO, 11), relief="solid", borderwidth=1,
                       bg=COLOR_FONDO, fg=COLOR_TEXTO, padx=8, pady=6)
        caja.insert("1.0", detalle)
        caja.configure(state="disabled")
        caja.pack(fill="x", pady=(4, 12))

        botones = ttk.Frame(self)
        botones.pack(fill="x")
        ttk.Button(botones, text="No, cancelar",
                   command=lambda: self._decidir("no")).pack(side="right")
        ttk.Button(botones, text="Permitir una vez",
                   command=lambda: self._decidir("permitir")).pack(side="right", padx=8)
        ttk.Button(botones, text=f"Permitir siempre ({clave})",
                   command=lambda: self._decidir("siempre")).pack(side="right")

        tk.Label(self, text="«Permitir siempre» aplica solo a este tipo de operación "
                            "y se olvida cuando cerrás la app.",
                 bg=COLOR_PANEL, fg=COLOR_TENUE, font=(FUENTE_UI, 10)).pack(anchor="w", pady=(8, 0))

        self.protocol("WM_DELETE_WINDOW", lambda: self._decidir("no"))
        self.bind("<Escape>", lambda _e: self._decidir("no"))
        self.grab_set()
        self.wait_window(self)

    def _decidir(self, valor: str):
        self.decision = valor
        self.destroy()


class DialogoElegirModelo(tk.Toplevel):
    """En modo combinado, elige con qué modelo se resuelve ESTE mensaje."""

    def __init__(self, padre, opciones):
        super().__init__(padre)
        self.eleccion = None
        self.opciones = opciones
        self.title("¿Con cuál lo resuelvo?")
        self.transient(padre)
        self.configure(bg=COLOR_PANEL, padx=18, pady=16)
        self.resizable(False, False)

        ttk.Label(self, text="¿Con qué modelo resuelvo este mensaje?",
                  font=(FUENTE_UI, 13, "bold")).pack(anchor="w")
        ttk.Label(self, text="Estás en modo combinado, así que se pregunta cada vez.",
                  foreground=COLOR_TENUE).pack(anchor="w", pady=(2, 10))

        self.lista = tk.Listbox(self, font=(FUENTE_UI, 12), height=len(opciones),
                                activestyle="none", relief="solid", borderwidth=1,
                                highlightthickness=0, exportselection=False)
        for etiqueta, _, _ in opciones:
            self.lista.insert("end", f" {etiqueta}")
        self.lista.selection_set(0)
        self.lista.pack(fill="x")
        self.lista.bind("<Double-Button-1>", lambda _e: self._aceptar())

        botones = ttk.Frame(self)
        botones.pack(fill="x", pady=(14, 0))
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(botones, text="Enviar", command=self._aceptar).pack(side="right", padx=8)

        self.bind("<Return>", lambda _e: self._aceptar())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.lista.focus_set()
        self.grab_set()
        self.wait_window(self)

    def _aceptar(self):
        seleccion = self.lista.curselection()
        if seleccion:
            self.eleccion = self.opciones[seleccion[0]]
        self.destroy()


class DialogoVersiones(tk.Toplevel):
    """Historial de versiones del código instalado (⌘0). Restaurar copia los
    archivos de vuelta; los proyectos, conversaciones y la biblioteca no se
    tocan nunca."""

    def __init__(self, padre, gestor: GestorVersiones):
        super().__init__(padre)
        self.gestor = gestor
        self.title("Versiones del agente")
        self.transient(padre)
        self.configure(bg=COLOR_PANEL, padx=18, pady=16)
        self.geometry("720x420")

        ttk.Label(self, text="Versiones guardadas del código",
                  font=(FUENTE_UI, 13, "bold")).pack(anchor="w")
        ttk.Label(self,
                  text="Se guarda una cada vez que el código cambia. Restaurar NO toca "
                       "tus proyectos, conversaciones ni la biblioteca.",
                  foreground=COLOR_TENUE, wraplength=660, justify="left").pack(anchor="w", pady=(2, 10))

        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True)

        self.lista = tk.Listbox(cuerpo, font=(FUENTE_MONO, 11), activestyle="none",
                                relief="solid", borderwidth=1, highlightthickness=0,
                                exportselection=False, width=46)
        self.lista.pack(side="left", fill="both", expand=False)
        self.lista.bind("<<ListboxSelect>>", self._al_elegir)

        self.detalle = tk.Text(cuerpo, wrap="word", relief="solid", borderwidth=1,
                               bg=COLOR_FONDO, fg=COLOR_TEXTO, padx=10, pady=8,
                               font=(FUENTE_MONO, 10), state="disabled")
        self.detalle.pack(side="left", fill="both", expand=True, padx=(10, 0))

        botones = ttk.Frame(self)
        botones.pack(fill="x", pady=(12, 0))
        ttk.Button(botones, text="Cerrar", command=self.destroy).pack(side="right")
        self.boton_restaurar = ttk.Button(botones, text="↩ Restaurar esta versión",
                                          command=self._restaurar, state="disabled")
        self.boton_restaurar.pack(side="right", padx=(0, 8))
        self.boton_borrar = ttk.Button(botones, text="🗑", width=3,
                                       command=self._borrar, state="disabled")
        self.boton_borrar.pack(side="right", padx=(0, 8))
        ttk.Button(botones, text="💾 Guardar versión actual",
                   command=self._guardar_ahora).pack(side="left")

        self.bind("<Escape>", lambda _e: self.destroy())
        self._refrescar()
        self.grab_set()

    def _refrescar(self):
        self.versiones = self.gestor.listar()
        self.lista.delete(0, "end")
        for v in self.versiones:
            self.lista.insert("end", f" {v['creado']}  ·  {v.get('etiqueta', '')[:22]}")
        if not self.versiones:
            self._mostrar("Todavía no hay versiones guardadas.\n\n"
                          "Se guarda una automáticamente cada vez que abrís la app "
                          "y el código cambió desde la última vez.")
        self.boton_restaurar.configure(state="disabled")
        self.boton_borrar.configure(state="disabled")

    def _mostrar(self, texto: str):
        self.detalle.configure(state="normal")
        self.detalle.delete("1.0", "end")
        self.detalle.insert("end", texto)
        self.detalle.configure(state="disabled")

    def _seleccionada(self):
        seleccion = self.lista.curselection()
        return self.versiones[seleccion[0]] if seleccion else None

    def _al_elegir(self, _evento=None):
        v = self._seleccionada()
        if not v:
            return
        self.boton_restaurar.configure(state="normal")
        self.boton_borrar.configure(state="normal")

        d = self.gestor.diferencias(v["id"])
        if "error" in d:
            self._mostrar(d["error"])
            return

        lineas = [f"Versión: {v['id']}", f"Guardada: {v['creado']}",
                  f"Etiqueta: {v.get('etiqueta', '—')}",
                  f"Archivos: {v.get('archivos', 0)}", ""]
        if not d["modificados"] and not d["restaurados"]:
            lineas.append("✅ Es idéntica al código que tenés instalado ahora.")
        else:
            if d["modificados"]:
                lineas.append(f"Volverían atrás ({len(d['modificados'])}):")
                lineas += [f"  ← {a}" for a in d["modificados"]]
                lineas.append("")
            if d["restaurados"]:
                lineas.append(f"Se recrearían ({len(d['restaurados'])}):")
                lineas += [f"  + {a}" for a in d["restaurados"]]
                lineas.append("")
            if d["nuevos_no_incluidos"]:
                lineas.append(f"Existen ahora y NO están en esta versión "
                              f"({len(d['nuevos_no_incluidos'])}); quedan como están:")
                lineas += [f"  ? {a}" for a in d["nuevos_no_incluidos"]]
        self._mostrar("\n".join(lineas))

    def _guardar_ahora(self):
        etiqueta = simpledialog.askstring("Guardar versión",
                                          "Etiqueta (para reconocerla después):",
                                          initialvalue="punto seguro", parent=self)
        if etiqueta is None:
            return
        resultado = self.gestor.crear_snapshot(etiqueta.strip() or "manual")
        if "error" in resultado:
            messagebox.showerror("Guardar versión", resultado["error"], parent=self)
            return
        self._refrescar()

    def _borrar(self):
        v = self._seleccionada()
        if not v:
            return
        if not messagebox.askokcancel("Borrar versión",
                                      f"Borrar la versión del {v['creado']}?",
                                      parent=self):
            return
        self.gestor.borrar(v["id"])
        self._refrescar()

    def _restaurar(self):
        v = self._seleccionada()
        if not v:
            return
        if not messagebox.askokcancel(
            "Restaurar versión",
            f"Volver el código al estado del {v['creado']}\n({v.get('etiqueta', '')})\n\n"
            "Tus proyectos, conversaciones y biblioteca NO se tocan.\n"
            "Antes de restaurar se guarda la versión actual, así que esto también "
            "se puede deshacer.\n\n"
            "Hay que cerrar y volver a abrir la app para que tome efecto.",
            parent=self
        ):
            return

        resultado = self.gestor.restaurar(v["id"])
        if "error" in resultado:
            messagebox.showerror("Restaurar versión", resultado["error"], parent=self)
            return

        if resultado.get("error_permisos"):
            messagebox.showerror(
                "Restaurar versión",
                "No tengo permiso de escritura sobre la instalación, así que la "
                "restauración quedó a medias.\n\n"
                + ("Se arregla una sola vez con:\n\n"
                   "sudo chown -R $(whoami) /Applications/AgenteDeepSeek"
                   if plataforma.ES_MAC else
                   "Revisá que tu usuario tenga permiso de escritura sobre la "
                   "carpeta de la app, o movela adentro de tu carpeta de usuario."),
                parent=self)
            return

        messagebox.showinfo(
            "Restaurar versión",
            f"Listo: {len(resultado['restaurados'])} archivo(s) restaurados.\n\n"
            "Cerrá y volvé a abrir AgenteDeepSeek para que tome efecto.",
            parent=self)
        self._refrescar()




class DialogoReenvio(tk.Toplevel):
    """Repeater estilo ZAP/Burp: la request completa arriba (editable), la
    respuesta abajo. 'Enviar' dispara y muestra la respuesta sin cerrar, para
    poder iterar. Cada envío se guarda; si descubre un path nuevo, la ventana
    padre lo mete en el árbol al cerrar."""

    def __init__(self, padre, almacen, flujo):
        super().__init__(padre)
        self.almacen = almacen
        self.flujo_id = flujo["id"]
        self.title(f"Reenviar · {flujo['metodo']} {flujo['ruta']}")
        self.geometry("820x680")
        self.configure(bg=COLOR_PANEL, padx=10, pady=10)
        self.transient(padre)

        cont = ttk.PanedWindow(self, orient="vertical")
        cont.pack(fill="both", expand=True)
        arriba = ttk.Labelframe(cont, text="Solicitud (editá lo que quieras)")
        cont.add(arriba, weight=1)
        self.editor = tk.Text(arriba, wrap="none", font=(FUENTE_MONO, 11),
                              relief="solid", borderwidth=1)
        self.editor.pack(fill="both", expand=True, padx=4, pady=4)
        self.editor.insert("1.0", self._request_a_texto(flujo))

        barra = ttk.Frame(self)
        barra.pack(fill="x", pady=6)
        self.boton_enviar = ttk.Button(barra, text="▶ Enviar", command=self._enviar)
        self.boton_enviar.pack(side="left")
        self.estado = ttk.Label(barra, text="", foreground=COLOR_TENUE)
        self.estado.pack(side="left", padx=10)
        ttk.Button(barra, text="Cerrar", command=self.destroy).pack(side="right")

        abajo = ttk.Labelframe(cont, text="Respuesta")
        cont.add(abajo, weight=1)
        self.resp = tk.Text(abajo, wrap="word", font=(FUENTE_MONO, 10),
                           relief="solid", borderwidth=1, state="disabled")
        self.resp.pack(fill="both", expand=True, padx=4, pady=4)
        self.resp.tag_configure("l1", foreground=COLOR_USUARIO, font=(FUENTE_MONO, 10, "bold"))
        self.resp.tag_configure("hd", foreground=COLOR_TENUE)
        self.resp.tag_configure("err", foreground=COLOR_ERROR)
        self.editor.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _request_a_texto(self, flujo):
        import json as _json
        q = f"?{flujo['query']}" if flujo["query"] else ""
        url = f"{flujo['esquema']}://{flujo['host']}:{flujo['puerto']}{flujo['ruta']}{q}"
        lineas = [f"{flujo['metodo']} {url}"]
        try:
            for k, v in _json.loads(
                    proxymod.secretos.decrypt(flujo["req_headers"]) or "[]"):
                lineas.append(f"{k}: {v}")
        except Exception:
            pass
        lineas.append("")
        cuerpo = flujo["req_body"]
        if isinstance(cuerpo, (bytes, bytearray)):
            cuerpo = cuerpo.decode("utf-8", "replace")
        lineas.append(cuerpo or "")
        return "\n".join(lineas)

    def _parsear(self):
        crudo = self.editor.get("1.0", "end").rstrip("\n")
        lineas = crudo.split("\n")
        if not lineas or " " not in lineas[0]:
            return None
        metodo, url = lineas[0].split(" ", 1)
        headers, i = [], 1
        while i < len(lineas) and lineas[i].strip():
            if ":" in lineas[i]:
                k, v = lineas[i].split(":", 1)
                headers.append([k.strip(), v.strip()])
            i += 1
        body = "\n".join(lineas[i + 1:]) if i + 1 <= len(lineas) else ""
        return {"metodo": metodo.strip(), "url": url.strip(),
                "headers": headers, "body": body}

    def _enviar(self):
        cambios = self._parsear()
        if cambios is None:
            self.estado.configure(text="La primera linea tiene que ser 'METODO url'",
                                  foreground=COLOR_ERROR)
            return
        self.boton_enviar.configure(state="disabled")
        self.estado.configure(text="enviando...", foreground=COLOR_TENUE)
        # El hilo SOLO hace la llamada bloqueante (nada de Tk). Un poller del
        # hilo principal recoge el resultado — así no hay 'main thread is not in
        # main loop' ni se congela la UI si la request tarda.
        self._resultado = None
        threading.Thread(
            target=lambda: setattr(self, "_resultado",
                                   proxymod.reenviar(self.almacen, self.flujo_id, cambios)),
            daemon=True).start()
        self._poll_resultado()

    def _poll_resultado(self):
        if getattr(self, "_resultado", None) is None:
            self.after(120, self._poll_resultado)
        else:
            self._mostrar_respuesta(self._resultado)

    def _mostrar_respuesta(self, r):
        self.boton_enviar.configure(state="normal")
        self.resp.configure(state="normal"); self.resp.delete("1.0", "end")
        if "error" in r:
            self.estado.configure(text="fallo", foreground=COLOR_ERROR)
            self.resp.insert("end", r["error"], "err")
            self.resp.configure(state="disabled")
            return
        self.estado.configure(text=f"{r['estado']} - {formato.duration(r['ms']/1000)}",
                              foreground=COLOR_AGENTE)
        f = self.almacen.obtener(r["id"])
        import json as _json
        self.resp.insert("end", f"{f['estado']}  -  {f['resp_tipo']}\n", "l1")
        try:
            for k, v in _json.loads(
                    proxymod.secretos.decrypt(f["resp_headers"]) or "[]"):
                self.resp.insert("end", f"{k}: {v}\n", "hd")
        except Exception:
            pass
        self.resp.insert("end", "\n")
        cuerpo = f["resp_body"]
        if isinstance(cuerpo, (bytes, bytearray)):
            cuerpo = cuerpo.decode("utf-8", "replace")
        self.resp.insert("end", (cuerpo or "")[:20000])
        self.resp.configure(state="disabled")


class AgenteUI(BASE_TK):
    def __init__(self):
        super().__init__()
        self.title("AgentFactory")
        self.geometry("1080x720")   # tamaño de respaldo si no se puede maximizar
        self.minsize(880, 560)
        self.configure(bg=COLOR_FONDO)
        self._aplicar_icono()
        # Arrancar maximizada. 'zoomed' anda en Windows/Linux; en macOS lanza
        # TclError y caemos a ocupar el área de pantalla completa.
        self.after(0, self._maximizar)

        self.cola = queue.Queue()
        sys.stdout = ColaSalida(self.cola, sys.stdout)
        sys.stderr = ColaSalida(self.cola, sys.stderr)

        self.gestor_conversaciones = GestorConversaciones()
        self.gestor_proyectos = GestorProyectos()
        self.biblioteca = Biblioteca()
        self.gestor_versiones = GestorVersiones(APP_DIR, RECURSOS_APP)
        self.proxy = None
        self.almacen_proxy = None
        self._flujos_pendientes = 0

        # Si el código cambió desde la última vez que abriste la app, queda
        # registrado antes de que uses nada. Es lo que hace que ⌘0 sirva.
        try:
            nuevo = self.gestor_versiones.snapshot_si_cambio("arranque")
            if nuevo and "error" not in nuevo:
                print(f"🗂  Versión guardada: {nuevo['id']} ({nuevo['archivos']} archivos)")
        except Exception as e:
            print(f"⚠️ No pude guardar la versión de arranque: {e}")

        self.conversacion = None      # la que estás MIRANDO ahora
        # Cada conversación abierta mantiene su propia instancia y su propio
        # estado de ocupado. Podés mirar una mientras otra sigue trabajando.
        self.instancias = {}          # {path: ConversacionChat}
        self.ocupadas = set()         # paths con un turno en curso
        self.inicio_turno = {}        # {path: timestamp} para el cronómetro
        self._marca_stream = {}       # {path: índice donde arranca el texto en vivo}
        self._marca_mensajes = {}     # {path: cuántos mensajes había al empezar}
        # Tope de continuaciones automáticas por mensaje tuyo, para que un
        # modelo que pregunta siempre no se quede girando solo.
        self.MAX_AUTO = 5
        self._auto_restantes = {}
        self.conversaciones = []
        self.proyectos = []
        self.agente_actual = None     # AgenteInteractivo corriendo, si hay
        self.ocupado_proyecto = False
        self.ocupado_procesador = False

        # Las operaciones destructivas las decide el usuario en la UI. El hilo
        # de trabajo se bloquea en un Event hasta que el diálogo responde.
        ejecucion.registrar_confirmador(self._confirmar_desde_hilo)

        self._construir_cabecera()
        self._construir_pestanas()

        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self.after(120, self._procesar_cola)

        # Refrescar: ⌘R en Mac, Ctrl+R y F5 por costumbre.
        for atajo in ("<Command-r>", "<Command-R>", "<Control-r>", "<F5>"):
            self.bind_all(atajo, self._al_refrescar)

        # ⌘0: historial de versiones / volver atrás un update.
        for atajo in ("<Command-0>", "<Control-0>"):
            self.bind_all(atajo, self._al_versiones)

        self._titulo_provisorio = None
        aviso = configuracion.asegurar_precios()
        if aviso:
            print(f'💲 {aviso}')

        self.refrescar_todo()
        self.mostrar_bienvenida()

    # -- layout ------------------------------------------------------------

    @property
    def ocupado_chat(self) -> bool:
        """¿Está trabajando la conversación que estás mirando? Otras pueden
        estar corriendo en paralelo sin bloquear esta ventana."""
        return (self.conversacion is not None
                and self.conversacion.conversacion_dir in self.ocupadas)

    def _aplicar_icono(self):
        """La ballena en la ventana, el Dock y los diálogos nativos.

        El lanzador corre python directo (no como el .app), así que macOS le
        pone el ícono genérico; hay que forzarlo. Es cosmético: si algo falla,
        se sigue sin drama."""
        png = None
        for cand in (APP_DIR / "icono.png", RECURSOS_APP / "icono.png" if RECURSOS_APP else None):
            if cand and cand.exists():
                png = cand
                break
        if png is None:
            return
        try:
            self._icono_img = tk.PhotoImage(file=str(png))
            self.iconphoto(True, self._icono_img)     # ventana y toplevels
        except Exception:
            pass
        if plataforma.ES_WINDOWS:
            from core import win_icono
            win_icono.set_taskbar_icon(self, APP_DIR / "icono.ico")
            return
        self.update_idletasks()                        # que exista NSApplication
        mac_icono.set_dock_icon(png)                # Dock + dialogos nativos

    def _construir_cabecera(self):
        cabecera = tk.Frame(self, bg=COLOR_PANEL, padx=16, pady=10)
        cabecera.pack(fill="x")

        tk.Label(cabecera, text="🏭 AgentFactory", bg=COLOR_PANEL, fg=COLOR_TEXTO,
                 font=(FUENTE_UI, 16, "bold")).pack(side="left")

        # Launcher del navegador de captura: un clic arranca el proxy (instala
        # mitmproxy la primera vez) y abre Firefox con un perfil dedicado.
        ttk.Button(cabecera, text="🦊 Abrir Firefox",
                   command=self.abrir_firefox_proxy).pack(side="left", padx=(14, 0))

        # Cuánto ocupa la captura en disco + un botón para vaciarla. La base de
        # flujos crece rápido navegando (llega a cientos de MB); tener el número
        # a la vista y el borrado a un clic evita que se vaya de las manos.
        ttk.Button(cabecera, text="🧹 Vaciar captura",
                   command=self.vaciar_captura).pack(side="left", padx=(6, 0))
        self.label_captura = tk.Label(cabecera, text="", bg=COLOR_PANEL,
                                      fg=COLOR_TENUE, font=(FUENTE_UI, 10))
        self.label_captura.pack(side="left", padx=(8, 0))
        self._refrescar_tamano_captura()

        # Probamos el proveedor configurado (DeepSeek o Claude) para mostrar cuál
        # está activo de verdad, no solo cuál está pedido en el .env.
        proveedor = crear_proveedor()
        if proveedor is not None:
            estado, color = f"● {proveedor.descripcion()}", COLOR_AGENTE
        else:
            estado = f"○ modo simulación ({proveedor_configurado()} sin credenciales)"
            color = COLOR_ERROR
        tk.Label(cabecera, text=estado, bg=COLOR_PANEL, fg=color,
                 font=(FUENTE_UI, 11)).pack(side="right")

        tk.Label(cabecera, text=f"{AGENT_CODE_DIR}  ", bg=COLOR_PANEL, fg=COLOR_TENUE,
                 font=(FUENTE_MONO, 10)).pack(side="right")

        tk.Label(cabecera, text=("⌘R refrescar · ⌘0 versiones   " if plataforma.ES_MAC
                                else "Ctrl+R refrescar · Ctrl+0 versiones   "), bg=COLOR_PANEL,
                 fg=COLOR_TENUE, font=(FUENTE_UI, 10)).pack(side="right")

    def _maximizar(self):
        try:
            self.state("zoomed")
        except tk.TclError:
            self.update_idletasks()
            self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")

    def _construir_pestanas(self):
        self.pestanas = ttk.Notebook(self)
        self.pestanas.pack(fill="both", expand=True, padx=12, pady=(10, 12))

        self.tab_chat = ttk.Frame(self.pestanas)
        self.tab_tareas = ttk.Frame(self.pestanas)
        self.tab_proyectos = ttk.Frame(self.pestanas)
        self.tab_biblioteca = ttk.Frame(self.pestanas)
        self.tab_procesador = ttk.Frame(self.pestanas)
        self.tab_config = ttk.Frame(self.pestanas)
        self.tab_proxy = ttk.Frame(self.pestanas)

        self.pestanas.add(self.tab_chat, text="  💬 Conversaciones  ")
        self.pestanas.add(self.tab_tareas, text="  🕒 Tareas  ")
        self.pestanas.add(self.tab_config, text="  ⚙️ Configuración  ")
        # Proyectos iterativos, Biblioteca, Procesador y Proxy: se construyen
        # (para no romper referencias internas) pero NO se muestran como pestañas.

        self._construir_tab_chat()
        self._construir_tab_tareas()
        self._construir_tab_proyectos()
        self._construir_tab_biblioteca()
        self._construir_tab_procesador()
        self._construir_tab_config()
        self._construir_tab_proxy()
        # Refrescar la lista de tareas al entrar a esa pestaña.
        self.pestanas.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)

    def _texto_scroll(self, padre, **kwargs):
        """Text + scrollbar, que es lo que se repite en las tres pestañas."""
        contenedor = ttk.Frame(padre)
        texto = tk.Text(contenedor, wrap="word", relief="solid", borderwidth=1,
                        bg=COLOR_FONDO, fg=COLOR_TEXTO, padx=12, pady=10,
                        insertbackground=COLOR_TEXTO, **kwargs)
        scroll = ttk.Scrollbar(contenedor, orient="vertical", command=texto.yview)
        texto.configure(yscrollcommand=scroll.set)
        texto.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return contenedor, texto

    # -- pestaña chat ------------------------------------------------------

    def _construir_tab_chat(self):
        panel = ttk.PanedWindow(self.tab_chat, orient="horizontal")
        panel.pack(fill="both", expand=True, pady=8)

        izquierda = ttk.Frame(panel, width=260)
        panel.add(izquierda, weight=0)

        ttk.Label(izquierda, text="Conversaciones", font=(FUENTE_UI, 12, "bold")).pack(anchor="w")
        self.lista_conversaciones = tk.Listbox(izquierda, font=(FUENTE_UI, 12),
                                               activestyle="none", relief="solid", borderwidth=1,
                                               highlightthickness=0, exportselection=False)
        self.lista_conversaciones.pack(fill="both", expand=True, pady=(6, 8))
        self.lista_conversaciones.bind("<<ListboxSelect>>", self._al_elegir_conversacion)

        botones = ttk.Frame(izquierda)
        botones.pack(fill="x")
        ttk.Button(botones, text="＋ Nueva", command=self.nueva_conversacion).pack(side="left")
        ttk.Button(botones, text="↻", width=3, command=self.refrescar_conversaciones).pack(side="left", padx=6)
        ttk.Button(botones, text="📂", width=3, command=self.abrir_workspace).pack(side="left")
        ttk.Button(botones, text="🗑", width=3, command=self.borrar_conversacion).pack(side="left", padx=6)

        self.total_gasto = ttk.Label(izquierda, text="", foreground=COLOR_TENUE,
                                     font=(FUENTE_UI, 11))
        self.total_gasto.pack(anchor="w", pady=(8, 0))

        ttk.Label(izquierda, text="Modelo de esta conversación",
                  font=(FUENTE_UI, 11, "bold")).pack(anchor="w", pady=(12, 2))
        self.modelo_chat = tk.StringVar(value=MODELOS_DISPONIBLES[0][0])
        self.combo_modelo = ttk.Combobox(
            izquierda, textvariable=self.modelo_chat, state="readonly",
            values=[e for e, _, _ in MODELOS_DISPONIBLES])
        self.combo_modelo.pack(fill="x")
        self.combo_modelo.bind("<<ComboboxSelected>>", self._al_cambiar_modelo)
        ttk.Label(izquierda, text="Se guarda por conversación.",
                  foreground=COLOR_TENUE, font=(FUENTE_UI, 10)).pack(anchor="w", pady=(2, 0))

        ttk.Label(izquierda, text="Workspace del agente",
                  font=(FUENTE_UI, 11, "bold")).pack(anchor="w", pady=(12, 2))
        self.workspace_label = ttk.Label(
            izquierda, text="—", foreground=COLOR_TENUE,
            font=(FUENTE_MONO, 10), wraplength=230, justify="left")
        self.workspace_label.pack(anchor="w", fill="x")
        fila_ws = ttk.Frame(izquierda)
        fila_ws.pack(fill="x", pady=(4, 0))
        ttk.Button(fila_ws, text="📁 Cambiar…",
                   command=self.elegir_workspace).pack(side="left")
        ttk.Button(fila_ws, text="Abrir",
                   command=self.abrir_workspace).pack(side="left", padx=(6, 0))
        ttk.Button(fila_ws, text="↺",
                   width=3, command=self.restablecer_workspace_ui).pack(side="left", padx=(6, 0))
        ttk.Label(izquierda,
                  text="Ahí guarda los scripts que genera. Se elige por conversación.",
                  foreground=COLOR_TENUE, font=(FUENTE_UI, 10),
                  wraplength=230, justify="left").pack(anchor="w", pady=(2, 0))

        ttk.Button(izquierda, text="📎 Adjuntar archivo…",
                   command=self.adjuntar_archivo).pack(fill="x", pady=(10, 0))
        ttk.Label(izquierda,
                  text=("Arrastrá archivos acá" if HAY_DND else "(drag & drop no disponible)"),
                  foreground=COLOR_TENUE, font=(FUENTE_UI, 10)).pack(anchor="w", pady=(2, 0))

        ttk.Button(izquierda, text="🕒 Programar tarea…",
                   command=self.abrir_programador).pack(fill="x", pady=(10, 0))
        ttk.Label(izquierda,
                  text="Que el agente corra solo, a una hora fija (lo despierta el sistema).",
                  foreground=COLOR_TENUE, font=(FUENTE_UI, 10),
                  wraplength=230, justify="left").pack(anchor="w", pady=(2, 0))

        derecha = ttk.Frame(panel)
        panel.add(derecha, weight=1)

        contenedor, self.transcripcion = self._texto_scroll(derecha, font=(FUENTE_UI, 13), state="disabled")
        contenedor.pack(fill="both", expand=True)

        # --- Globitos estilo WhatsApp -------------------------------------
        # Fondo del chat (beige clásico) y dos globos: el tuyo a la derecha en
        # verde, el del agente a la izquierda en blanco. En un widget Text los
        # globos son bandas alineadas con padding (no se pueden redondear las
        # esquinas), pero leen claramente como conversación de chat.
        GLOBO_USER, GLOBO_AGENT = "#DCF8C6", "#FFFFFF"
        GLOBO_TXT, GLOBO_HORA = "#111B21", "#667781"
        self.transcripcion.configure(bg="#ECE5DD")
        self.transcripcion.tag_configure(
            "globo_user", background=GLOBO_USER, foreground=GLOBO_TXT, justify="right",
            lmargin1=150, lmargin2=150, rmargin=16, spacing1=5, spacing3=5,
            borderwidth=10, relief="flat")
        self.transcripcion.tag_configure(
            "globo_agent", background=GLOBO_AGENT, foreground=GLOBO_TXT, justify="left",
            lmargin1=16, lmargin2=16, rmargin=150, spacing1=5, spacing3=5,
            borderwidth=10, relief="flat")
        self.transcripcion.tag_configure(
            "hora_user", justify="right", foreground=GLOBO_HORA,
            font=(FUENTE_MONO, 9), rmargin=18, spacing3=10)
        self.transcripcion.tag_configure(
            "hora_agent", justify="left", foreground=GLOBO_HORA,
            font=(FUENTE_MONO, 9), lmargin1=18, lmargin2=18, spacing3=10)

        self.transcripcion.tag_configure("rol_usuario", foreground=COLOR_USUARIO,
                                         font=(FUENTE_UI, 12, "bold"), spacing1=10)
        self.transcripcion.tag_configure("rol_agente", foreground=COLOR_AGENTE,
                                         font=(FUENTE_UI, 12, "bold"), spacing1=10)
        self.transcripcion.tag_configure("cuerpo", foreground=COLOR_TEXTO, spacing3=6)
        self.transcripcion.tag_configure("tool", foreground=COLOR_TENUE, font=(FUENTE_MONO, 10))
        self.transcripcion.tag_configure("aviso", foreground=COLOR_TENUE,
                                         font=(FUENTE_UI, 11, "italic"))
        self.transcripcion.tag_configure("arte", foreground=COLOR_AGENTE,
                                         font=(FUENTE_MONO, 11), spacing1=2)
        self.transcripcion.tag_configure("titulo_aviso", foreground=COLOR_TEXTO,
                                         font=(FUENTE_UI, 13, "bold"))
        # Timestamp que acompaña a cada encabezado (Tú / Agente).
        self.transcripcion.tag_configure("hora", foreground=COLOR_TENUE,
                                         font=(FUENTE_MONO, 10))
        # Línea divisoria entre intercambios.
        self.transcripcion.tag_configure("divisoria", foreground=COLOR_TENUE,
                                         justify="center", spacing1=8, spacing3=8)
        # Ícono para copiar cada respuesta del agente: discreto (tenue), con
        # cursor de mano y feedback al tocarlo.
        self.transcripcion.tag_configure("copiar", foreground=COLOR_TENUE)
        self.transcripcion.tag_bind(
            "copiar", "<Enter>", lambda _e: self.transcripcion.configure(cursor="hand2"))
        self.transcripcion.tag_bind(
            "copiar", "<Leave>", lambda _e: self.transcripcion.configure(cursor=""))
        self.transcripcion.tag_bind("copiar", "<Button-1>", self._al_click_copiar)
        self._copiables = {}   # {clave -> texto a copiar}
        self._n_copia = 0
        render_markdown.configurar_tags(
            self.transcripcion, fuente_ui=FUENTE_UI, fuente_mono=FUENTE_MONO,
            color_texto=COLOR_TEXTO, color_tenue=COLOR_TENUE,
            color_acento=COLOR_USUARIO, color_fondo_codigo=COLOR_PANEL)

        entrada_frame = ttk.Frame(derecha)
        entrada_frame.pack(fill="x", pady=(8, 0))

        self.entrada = tk.Text(entrada_frame, height=3, wrap="word", font=(FUENTE_UI, 13),
                               relief="solid", borderwidth=1, padx=10, pady=8)
        self.entrada.pack(side="left", fill="both", expand=True)
        self.entrada.bind("<Return>", self._al_enter)
        self.entrada.bind("<KeyPress>", self._al_teclear)
        self.entrada.bind("<Shift-Return>", lambda _e: None)

        # Autocompletado de sitios con '@' DESACTIVADO a pedido del usuario:
        # se sentía inestable al escribir. Se deja en None (no se instancia el
        # CompletadorSitios ni se enganchan las teclas de navegación) para que
        # las flechas, Escape y Tab vuelvan a comportarse como en cualquier
        # cuadro de texto. Para reactivarlo, volver a crear el completador acá.
        self.completador_sitios = None

        columna_botones = ttk.Frame(entrada_frame)
        columna_botones.pack(side="left", padx=(8, 0), fill="y")
        self.boton_enviar = ttk.Button(columna_botones, text="Enviar", command=self.enviar_mensaje)
        self.boton_enviar.pack(fill="x")
        self.boton_detener_chat = ttk.Button(columna_botones, text="⏹ Detener",
                                             command=self.detener_chat, state="disabled")
        self.boton_detener_chat.pack(fill="x", pady=(4, 0))

        # 'Fraccionar' quitado de la UI. Se conserva la variable (siempre en
        # False) para no romper referencias internas de carga de conversación.
        self.fraccionar = tk.BooleanVar(value=False)

        self.admin_habilitado = tk.BooleanVar(value=False)
        self.check_admin = ttk.Checkbutton(
            columna_botones, text="Admin", variable=self.admin_habilitado,
            command=self._al_cambiar_admin)
        # Solo donde la elevacion existe de verdad. En Windows haria falta una
        # vuelta por UAC que no esta implementada, y el control quedaria
        # prometiendo algo que siempre falla.
        if plataforma.soporta_elevacion():
            self.check_admin.pack(anchor="w")

        # Proxy quitado de la UI: se mantiene la variable para no romper
        # referencias internas (guardado/carga de conversación), sin control visible.
        self.proxy_secretos = tk.BooleanVar(value=False)

        self.auto_continuar = tk.BooleanVar(value=False)
        ttk.Checkbutton(columna_botones, text="Sí a todo", variable=self.auto_continuar,
                        command=self._al_cambiar_auto).pack(anchor="w")

        # Aparece solo cuando el agente cortó preguntando; es la alternativa de
        # un clic para cuando NO querés el automático.
        self.boton_seguir = ttk.Button(columna_botones, text="▶ Sí, seguí",
                                       command=self.continuar_turno)
        self.boton_seguir.pack(fill="x", pady=(6, 0))
        self.boton_seguir.pack_forget()

        self.estado_chat = ttk.Label(derecha, text="", foreground=COLOR_TENUE)
        self.estado_chat.pack(anchor="w", pady=(4, 0))

        # Drag & drop de archivos: se sueltan sobre la caja de texto o sobre la
        # transcripción, y se pegan las rutas absolutas en el mensaje.
        if HAY_DND:
            for widget in (self.entrada, self.transcripcion):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._al_soltar_archivos)

    def _al_teclear(self, evento):
        """Si te ponés a escribir sin conversación abierta, se crea una sola.

        El título definitivo se pone recién al enviar, cuando ya sabemos de qué
        se trata: acá todavía hay una sola tecla escrita."""
        if self.conversacion is not None:
            return
        # Solo con teclas que producen texto; ignora flechas, ⌘, Escape…
        if not evento.char or not evento.char.isprintable():
            return
        try:
            ruta = self.gestor_conversaciones.crear_conversacion("")
        except Exception as e:
            messagebox.showerror("AgentFactory", f"No pude crear la conversación:\n{e}")
            return
        self._titulo_provisorio = ruta
        self.refrescar_conversaciones()
        self._cargar_conversacion(ruta)
        for i, c in enumerate(self.conversaciones):
            if c["path"] == ruta:
                self.lista_conversaciones.selection_clear(0, "end")
                self.lista_conversaciones.selection_set(i)
                break
        self.entrada.focus_set()

    def _al_enter(self, _evento):
        # Con la lista de sitios abierta, Enter elige la opción en vez de
        # mandar el mensaje: mandarlo a medio completar sería lo contrario de
        # lo que la lista viene a evitar.
        if getattr(self, "completador_sitios", None) and self.completador_sitios.al_enter():
            return "break"
        self.enviar_mensaje()
        return "break"  # Enter envía; Shift+Enter hace salto de línea

    def _refrescar_tamano_captura(self):
        """Actualiza el label de tamaño de la captura y se reprograma. Cada 3 s
        alcanza: no hace falta verlo latir tecla a tecla, pero sí que baje en
        cuanto vaciás y que suba mientras navegás."""
        try:
            from core import proxy_tool
            from core import i18n
            bytes_ = proxy_tool.tamano_captura()
            texto = (i18n.t("captura.tamano", tamano=formato.size(bytes_))
                     if bytes_ else i18n.t("captura.vacia"))
        except Exception:
            texto = ""
        if getattr(self, "label_captura", None) is not None:
            try:
                self.label_captura.configure(text=texto)
            except tk.TclError:
                return
        self.after(3000, self._refrescar_tamano_captura)

    def vaciar_captura(self):
        """Borra todos los flujos capturados, tras confirmar."""
        from core import proxy_tool
        bytes_ = proxy_tool.tamano_captura()
        if not bytes_:
            messagebox.showinfo("Vaciar captura", "La captura ya está vacía.", parent=self)
            return
        if not messagebox.askokcancel(
                "Vaciar captura",
                f"Se van a borrar TODOS los flujos capturados ({formato.size(bytes_)}).\n\n"
                "Esto no toca tus conversaciones ni tareas, solo lo que navegaste "
                "con Firefox. No se puede deshacer.\n\n¿Vaciar?",
                icon="warning", parent=self):
            return
        r = proxy_tool.vaciar_captura()
        self._refrescar_tamano_captura()
        if r.get("ok"):
            messagebox.showinfo(
                "Vaciar captura",
                f"Listo: se borraron {r.get('borrados', 0)} flujos.", parent=self)
        else:
            messagebox.showerror(
                "Vaciar captura", f"No pude vaciar del todo:\n{r.get('error')}", parent=self)

    def _sitios_para_completar(self):
        """Los sitios capturados, para la lista de autocompletado.

        Se consulta en el momento de abrir la lista y no al arrancar la app:
        la captura crece mientras navegás."""
        try:
            from core import proxy_tool
            return proxy_tool.listar_sitios_capturados().get("sitios", [])
        except Exception:
            return []

    def mostrar_bienvenida(self):
        """Ballena + estado. Si falta la API key, lo dice fuerte y manda a
        Configuración: sin clave no se puede hacer nada y conviene que se note
        antes de que la persona escriba un mensaje que no va a ir a ningún lado."""
        proveedor = crear_proveedor()
        self.transcripcion.configure(state="normal")
        self.transcripcion.delete("1.0", "end")
        self.transcripcion.configure(state="disabled")
        self.transcripcion._hubo_intercambio = False
        self._copiables.clear()
        self.transcripcion._imagenes_retenidas = []

        for texto, tag in bienvenida.initial_text(
                hay_credenciales=proveedor is not None,
                proveedor=proveedor.descripcion() if proveedor else "",
                n_conversaciones=len(self.conversaciones),
                ruta_env=rutas.ruta_env()):
            self.transcripcion.configure(state="normal")
            self.transcripcion.insert("end", texto, tag)
            self.transcripcion.configure(state="disabled")

        if proveedor is None:
            self.pestanas.select(self.tab_config)

    def refrescar_conversaciones(self):
        self.conversaciones = self.gestor_conversaciones.listar_conversaciones()
        self.lista_conversaciones.delete(0, "end")
        for c in self.conversaciones:
            marca = "⏳ " if c["path"] in self.ocupadas else ""
            # El gasto va al final de cada línea; sin precios cargados no se
            # inventa un número, se muestra la cantidad de tokens.
            if c.get("costo"):
                # '~' = estimado a partir de los tokens, porque esa conversación
                # es anterior a que la app registrara el costo turno a turno.
                aprox = "~" if c.get("costo_estimado") else ""
                gasto = f"  ·  {aprox}{formato.money(c['costo'])}"
            else:
                gasto = ""
            self.lista_conversaciones.insert(
                "end", f" {marca}{c['titulo']}  ({c['mensajes']}){gasto}")
        # Suma de todo lo gastado, sobre todas las conversaciones.
        tokens = sum(c.get("tokens", 0) for c in self.conversaciones)
        total = sum(c.get("costo", 0.0) for c in self.conversaciones)
        hay_estimados = any(c.get("costo_estimado") for c in self.conversaciones)
        if tokens:
            self.total_gasto.configure(
                text=f"Total: {formato.compact(tokens)} tokens  ·  "
                     f"{'~' if hay_estimados else ''}{formato.money(total)}")
        else:
            self.total_gasto.configure(text="")



    def nueva_conversacion(self):
        titulo = simpledialog.askstring("Nueva conversación",
                                        "Nombre (Enter para uno automático):", parent=self)
        if titulo is None:
            return
        conversacion_dir = self.gestor_conversaciones.crear_conversacion(titulo.strip())
        self.refrescar_conversaciones()
        for i, c in enumerate(self.conversaciones):
            if c["path"] == conversacion_dir:
                self.lista_conversaciones.selection_clear(0, "end")
                self.lista_conversaciones.selection_set(i)
                self._cargar_conversacion(conversacion_dir)
                break
        self.entrada.focus_set()

    def _al_elegir_conversacion(self, _evento):
        seleccion = self.lista_conversaciones.curselection()
        if not seleccion:
            return
        self._cargar_conversacion(self.conversaciones[seleccion[0]]["path"])

    def _cargar_conversacion(self, conversacion_dir: Path):
        try:
            # Si esta conversación ya está abierta (y quizá corriendo), se
            # reusa su instancia: recrearla perdería el turno en curso, sus
            # permisos y sus métricas.
            if conversacion_dir in self.instancias:
                self.conversacion = self.instancias[conversacion_dir]
            else:
                self.conversacion = ConversacionChat(conversacion_dir)
                self.instancias[conversacion_dir] = self.conversacion
        except Exception as e:
            messagebox.showerror("AgentFactory", f"No pude abrir la conversación:\n{e}")
            return

        self.transcripcion.configure(state="normal")
        self.transcripcion.delete("1.0", "end")
        self.transcripcion.configure(state="disabled")
        self.transcripcion._hubo_intercambio = False
        self._copiables.clear()

        meta = self.conversacion.meta
        self.modelo_chat.set(etiqueta_de(meta.get('proveedor', ''), meta.get('modelo', '')))
        self.fraccionar.set(bool(meta.get('fraccionar', False)))
        self.admin_habilitado.set(bool(getattr(self.conversacion, 'admin_habilitado', False)))
        self.proxy_secretos.set(bool(getattr(self.conversacion, 'proxy_secretos', False)))

        herramientas = self.conversacion.herramientas_disponibles()
        n_bib = self.biblioteca.listar().get("total", 0)
        self._aviso_transcripcion(
            f"Workspace: {self.conversacion.workspace_dir}\n"
            f"Herramientas: {len(herramientas)} · Biblioteca: {n_bib} módulo(s)"
        )

        for m in self.conversacion.mensajes:
            rol = m.get("role")
            contenido = (m.get("content") or "").strip()
            if rol == "user" and contenido:
                self._agregar_mensaje("Tú", contenido, "rol_usuario", m.get("ts"))
            elif rol == "assistant" and contenido:
                self._agregar_mensaje("Agente", contenido, "rol_agente", m.get("ts"))

        self._actualizar_workspace_label()
        self._sincronizar_controles_chat()

    def enviar_mensaje(self):
        if self.ocupado_chat:
            return
        if self.conversacion is None:
            messagebox.showinfo("AgentFactory", "Elegí o creá una conversación primero.")
            return

        texto = self.entrada.get("1.0", "end").strip()
        if not texto:
            return

        if self.conversacion.meta.get("proveedor") == "combinado":
            if not self._elegir_modelo_del_mensaje():
                return

        self.entrada.delete("1.0", "end")

        # La conversación creada al empezar a teclear todavía no tiene
        # nombre: recién ahora sabemos de qué se trata.
        if getattr(self, "_titulo_provisorio", None) == self.conversacion.conversacion_dir:
            titulo = bienvenida.title_from_text(texto)
            if titulo:
                self.conversacion.meta["titulo"] = titulo
                self.conversacion._persistir()
            self._titulo_provisorio = None

        # Un mensaje tuyo reinicia el presupuesto de continuaciones automáticas.
        self._auto_restantes[self.conversacion.conversacion_dir] = self.MAX_AUTO
        self._enviar_texto(texto)

    def _enviar_texto(self, texto: str, visible: str = None):
        """Envía sin pasar por la caja de entrada. Lo usan el botón «Sí, seguí»
        y el modo automático; `visible` es lo que se muestra en pantalla cuando
        conviene que no sea el texto literal que viaja al modelo."""
        if self.ocupado_chat or self.conversacion is None:
            return
        self.boton_seguir.pack_forget()
        self._agregar_mensaje("Tú", visible or texto, "rol_usuario")

        conversacion = self.conversacion
        # Marcas para poder reemplazar el texto en vivo por el Markdown
        # renderizado cuando termine el turno.
        # El texto en vivo se pinta plano; al cerrar el turno se reemplaza por el
        # globo del agente ya renderizado (ver _cerrar_stream).
        self.transcripcion.configure(state="normal")
        self._marca_stream[conversacion.conversacion_dir] = self.transcripcion.index("end-1c")
        self.transcripcion.configure(state="disabled")
        self._marca_mensajes[conversacion.conversacion_dir] = len(conversacion.mensajes)
        self.ocupadas.add(conversacion.conversacion_dir)
        self.inicio_turno[conversacion.conversacion_dir] = time.time()
        self._sincronizar_controles_chat()
        self._tic_metricas()

        threading.Thread(target=self._worker_chat,
                         args=(conversacion, texto), daemon=True).start()

    def borrar_conversacion(self):
        seleccion = self.lista_conversaciones.curselection()
        if not seleccion:
            messagebox.showinfo("Borrar conversación", "Elegí primero una conversación de la lista.")
            return
        if self.ocupado_chat:
            messagebox.showwarning("Borrar conversación",
                                   "Esperá a que termine el turno en curso.")
            return

        c = self.conversaciones[seleccion[0]]
        if not messagebox.askokcancel(
            "Borrar conversación",
            f"«{c['titulo']}»\n\n"
            f"{c['mensajes']} mensaje(s) · {_tamano_legible(c['path'])}\n"
            f"{c['path']}\n\n"
            "Se borra el historial Y el workspace de esa conversación.\n"
            "Va a la Papelera, así que se puede recuperar."
        ):
            return

        try:
            destino = _mover_a_papelera(c["path"])
        except Exception as e:
            messagebox.showerror("Borrar conversación", f"No pude borrarla:\n{e}")
            return

        if self.conversacion is not None and self.conversacion.conversacion_dir == c["path"]:
            self.conversacion = None
            self.transcripcion.configure(state="normal")
            self.transcripcion.delete("1.0", "end")
            self.transcripcion.configure(state="disabled")
            self.transcripcion._hubo_intercambio = False

        self._log(f"🗑 Conversación «{c['titulo']}» movida a la Papelera: {destino}")
        self.refrescar_conversaciones()

    def _elegir_modelo_del_mensaje(self) -> bool:
        """En modo combinado se pregunta, mensaje por mensaje, con qué modelo
        resolverlo. Devuelve False si cancelás."""
        opciones = [(e, p, m) for e, p, m in MODELOS_DISPONIBLES if p != 'combinado']
        elegido = DialogoElegirModelo(self, opciones).eleccion
        if elegido is None:
            return False
        _, proveedor, modelo = elegido
        nuevo = crear_proveedor(proveedor)
        if nuevo is None:
            messagebox.showwarning('Modelo', f"No pude activar '{proveedor}': faltan credenciales.")
            return False
        nuevo.modelo = modelo
        self.conversacion.proveedor = nuevo
        self._aviso_transcripcion(f'→ este mensaje lo resuelve {nuevo.descripcion()}')
        return True

    def _al_cambiar_fraccionar(self):
        """Sin tildar, la pregunta va tal cual: 'descomponer_pregunta' no se le
        ofrece al modelo y su schema tampoco viaja en la request."""
        if self.conversacion is None:
            return
        activo = bool(self.fraccionar.get())
        self.conversacion.fraccionar = activo
        self.conversacion.meta['fraccionar'] = activo
        self.conversacion._persistir()
        self._aviso_transcripcion(
            'Fraccionar ACTIVADO: las consultas largas se parten en sub-preguntas.'
            if activo else
            'Fraccionar desactivado: la pregunta se manda tal cual (~368 tokens menos por mensaje).')

    def _al_cambiar_auto(self):
        """Modo 'sí a todo': cuando el agente corta preguntando si sigue, se le
        responde solo. NO afecta a los diálogos de borrado ni a los de admin:
        esos son otro mecanismo y siguen preguntando siempre."""
        if self.auto_continuar.get():
            self._aviso_transcripcion(
                f"Sí a todo ACTIVADO: si el agente pregunta si sigue, le respondo yo "
                f"(máximo {self.MAX_AUTO} veces por mensaje tuyo). "
                f"Las confirmaciones de borrado y de administrador te las sigo preguntando.")
        else:
            self._aviso_transcripcion("Sí a todo desactivado.")

    def continuar_turno(self):
        """Manda la respuesta de continuación, venga del botón o del automático."""
        if self.ocupado_chat or self.conversacion is None:
            return
        self.boton_seguir.pack_forget()
        self._enviar_texto(
            "Sí, seguí con lo que propusiste. Si en realidad necesitabas que "
            "eligiera entre alternativas, no asumas: pará y preguntame concreto.",
            visible="▶ (sí, seguí)")

    def _al_cambiar_secretos(self):
        """Cuando el flujo del proxy entra a la conversación, incluir los valores
        reales de sesión/tokens (en vez de redactarlos). Es lo que hace falta
        para razonar sobre auth, pero esos valores VIAJAN al proveedor del LLM."""
        if self.conversacion is None:
            self.proxy_secretos.set(False)
            return
        activo = bool(self.proxy_secretos.get())
        if activo and not messagebox.askokcancel(
                "Incluir secretos del proxy",
                "Con esto, cuando traigas un flujo del proxy a la conversación "
                "(@flujo N), sus cookies de sesión y tokens van SIN redactar.\n\n"
                "Eso es lo que necesitás para razonar sobre autenticación —pero "
                "esos valores viajan al proveedor del modelo "
                f"({crear_proveedor().nombre if crear_proveedor() else 'el LLM'}) "
                "como parte del mensaje.\n\n"
                "Activalo solo para pruebas autorizadas y con datos que puedas "
                "mandar a un tercero. ¿Continuar?"):
            self.proxy_secretos.set(False)
            return
        self.conversacion.proxy_secretos = activo
        self.conversacion.meta["proxy_secretos"] = activo
        self.conversacion._persistir()
        self._aviso_transcripcion(
            "🔓 Secretos del proxy ACTIVADOS: los flujos entran con cookies/tokens "
            "reales (viajan al LLM)." if activo else
            "Secretos del proxy desactivados: los flujos entran redactados.")

    def _al_cambiar_admin(self):
        """Habilita que el agente pueda pedir root. No se guarda en disco:
        vuelve a apagarse cada vez que abrís la app."""
        if self.conversacion is None:
            self.admin_habilitado.set(False)
            return
        activo = bool(self.admin_habilitado.get())

        if activo and not messagebox.askokcancel(
            "Permitir ejecución como administrador",
            "El agente va a poder pedir correr comandos como root.\n\n"
            "Cada vez que lo haga vas a ver: primero qué comando es y sobre qué "
            "rutas opera, y después el diálogo de autenticación de macOS.\n\n"
            "Tu contraseña la maneja el sistema — esta app nunca la ve.\n\n"
            "Como root no hay Papelera: lo que se borra, se borra.\n\n"
            "¿Habilitar para esta conversación?"):
            self.admin_habilitado.set(False)
            return

        self.conversacion.admin_habilitado = activo
        self._aviso_transcripcion(
            "Admin ACTIVADO para esta conversación (se apaga al cerrar la app)."
            if activo else "Admin desactivado.")

    def detener_chat(self):
        if self.conversacion is None or not self.ocupado_chat:
            return
        self.conversacion.cancelar()
        self.estado_chat.configure(text="⏹ Deteniendo…")
        self.boton_detener_chat.configure(state="disabled")

    # -- métricas y estado de los controles --------------------------------

    @staticmethod
    def _formato_tokens(n: int) -> str:
        return formato.compact(n)

    def _texto_metricas(self, conversacion, en_curso: bool) -> str:
        uso = conversacion.uso_turno if en_curso else conversacion.uso_total
        if not en_curso and not uso.get("llamadas"):
            return ""

        if en_curso and conversacion.conversacion_dir in self.inicio_turno:
            segundos = time.time() - self.inicio_turno[conversacion.conversacion_dir]
        else:
            segundos = uso["segundos"]

        # El cronómetro corre desde el arranque; los tokens recién existen
        # cuando vuelve la primera llamada.
        partes = [formato.duration(segundos)]
        if not uso.get("llamadas"):
            return partes[0]
        partes += [f"{self._formato_tokens(uso['entrada'] + uso['salida'])} tokens",
                   f"{uso['llamadas']} llamada(s)"]
        # Si falta el precio de algún modelo usado, no inventamos un total.
        if uso.get("costo_conocido"):
            partes.append(formato.money(uso["costo"]))
        else:
            partes.append("US$ — (cargá los precios en el .env)")
        return " · ".join(partes)

    def _sincronizar_controles_chat(self):
        """Deja los botones y el estado acordes a la conversación que estás
        MIRANDO, no a la que esté trabajando."""
        ocupada = self.ocupado_chat
        self.boton_enviar.configure(state="disabled" if ocupada else "normal")
        self.boton_detener_chat.configure(state="normal" if ocupada else "disabled")

        if self.conversacion is None:
            self.estado_chat.configure(text="")
            return

        metricas = self._texto_metricas(self.conversacion, ocupada)
        if ocupada:
            self.estado_chat.configure(
                text=f"⏳ El agente está prediciendo…   {metricas}")
        else:
            corriendo = len(self.ocupadas)
            aviso = f"   ·   {corriendo} conversación(es) trabajando en segundo plano" if corriendo else ""
            self.estado_chat.configure(
                text=(f"Total de esta conversación: {metricas}{aviso}" if metricas else aviso.strip(" ·")))

    def _tic_metricas(self):
        """Refresca los números mientras hay algo corriendo (cada medio segundo)."""
        if self.ocupado_chat:
            self._sincronizar_controles_chat()
        if self.ocupadas:
            self.after(500, self._tic_metricas)

    def _confirmar_desde_hilo(self, resumen: str, detalle: str, clave: str) -> str:
        """Lo llama core.ejecucion desde el hilo de trabajo. Manda el pedido a la
        cola de la UI y se queda esperando la decisión."""
        if threading.current_thread() is threading.main_thread():
            return DialogoConfirmacion(self, resumen, detalle, clave).decision
        evento, caja = threading.Event(), []
        self.cola.put(("confirmar", (resumen, detalle, clave, evento, caja)))
        # Con techo: si la ventana muriera, el hilo no queda colgado para siempre.
        if not evento.wait(timeout=300):
            return "no"
        return caja[0] if caja else "no"

    def _al_cambiar_modelo(self, _evento=None):
        if self.conversacion is None:
            return
        proveedor, modelo = desde_etiqueta(self.modelo_chat.get())
        self.conversacion.meta["proveedor"] = proveedor
        self.conversacion.meta["modelo"] = modelo
        self.conversacion._persistir()
        if proveedor == "combinado":
            self._aviso_transcripcion(
                "Modelo: combinado — te voy a preguntar en cada mensaje con cuál resolverlo.")
            return
        nuevo = crear_proveedor(proveedor or None)
        if nuevo is None:
            messagebox.showwarning(
                "Modelo",
                f"No pude activar '{proveedor}': faltan credenciales en el .env.\n"
                f"La conversación sigue con el anterior.")
            return
        if modelo:
            nuevo.modelo = modelo
        self.conversacion.proveedor = nuevo
        self._aviso_transcripcion(f"Modelo de esta conversación: {nuevo.descripcion()}")

    # -- Workspace del agente (carpeta donde crea/edita/ejecuta) -----------

    @staticmethod
    def _acortar_ruta(ruta) -> str:
        """Muestra ~ en vez del home para que la ruta entre en la columna."""
        s = str(ruta)
        home = str(Path.home())
        if s == home or s.startswith(home + "/"):
            s = "~" + s[len(home):]
        return s

    def _actualizar_workspace_label(self):
        if self.conversacion is None:
            self.workspace_label.configure(text="— (elegí una conversación)")
            return
        marca = "" if self.conversacion.workspace_es_por_defecto() else "★ "
        self.workspace_label.configure(
            text=f"{marca}{self._acortar_ruta(self.conversacion.workspace_dir)}")

    def elegir_workspace(self):
        if self.conversacion is None:
            messagebox.showinfo("Workspace", "Elegí o creá una conversación primero.")
            return
        from tkinter import filedialog
        destino = filedialog.askdirectory(
            parent=self, title="Elegí la carpeta de trabajo del agente",
            initialdir=str(self.conversacion.workspace_dir), mustexist=False)
        if not destino:
            return
        nuevo = self.conversacion.establecer_workspace(destino)
        self._actualizar_workspace_label()
        self._aviso_transcripcion(
            f"📁 Workspace de esta conversación: {nuevo}\n"
            "Ahí van a parar los archivos que genere el agente (los scripts, etc.).")

    def abrir_workspace(self):
        if self.conversacion is None:
            return
        error = plataforma.abrir(str(self.conversacion.workspace_dir))
        if error:
            messagebox.showwarning("Abrir workspace", error)

    def restablecer_workspace_ui(self):
        if self.conversacion is None or self.conversacion.workspace_es_por_defecto():
            return
        nuevo = self.conversacion.restablecer_workspace()
        self._actualizar_workspace_label()
        self._aviso_transcripcion(f"📁 Workspace vuelto al de la conversación: {nuevo}")

    def _rutas_soltadas(self, data: str):
        """tkinter entrega las rutas separadas por espacios, y entre llaves las
        que tienen espacios adentro."""
        rutas, actual, en_llave = [], "", False
        for ch in data:
            if ch == "{":
                en_llave = True
            elif ch == "}":
                en_llave = False
                rutas.append(actual); actual = ""
            elif ch == " " and not en_llave:
                if actual:
                    rutas.append(actual); actual = ""
            else:
                actual += ch
        if actual:
            rutas.append(actual)
        return [r for r in rutas if r]

    def _al_soltar_archivos(self, evento):
        self._agregar_rutas(self._rutas_soltadas(evento.data))
        return "break"

    def adjuntar_archivo(self):
        from tkinter import filedialog
        rutas = filedialog.askopenfilenames(parent=self, title="Adjuntar archivos")
        if rutas:
            self._agregar_rutas(list(rutas))

    def _agregar_rutas(self, rutas):
        """Pega las rutas absolutas en el mensaje. El agente tiene ejecución real,
        así que con la ruta le alcanza para ir a leerlas él mismo — no hace falta
        copiar los archivos a ningún lado."""
        utiles = []
        for r in rutas:
            p = Path(r.strip().strip('"'))
            if p.exists():
                tipo = "carpeta" if p.is_dir() else _tamano_legible(p) if p.is_file() else ""
                utiles.append(f"{p}  ({tipo})" if tipo else str(p))
            else:
                self._log(f"⚠️ No encontré: {r}")
        if not utiles:
            return

        actual = self.entrada.get("1.0", "end").strip()
        bloque = "\n".join(f"- {u}" for u in utiles)
        prefijo = f"{actual}\n\n" if actual else ""
        self.entrada.delete("1.0", "end")
        self.entrada.insert("1.0",
                            f"{prefijo}Archivos:\n{bloque}\n\n")
        self.entrada.see("end")
        self.entrada.focus_set()
        self._log(f"📎 {len(utiles)} archivo(s) agregados al mensaje")

    def _evaluar_continuacion(self, ruta, contenido):
        """Si el agente cortó preguntando, ofrece seguir — o sigue solo."""
        from core.chat import parece_pausa
        if self.conversacion is None or self.conversacion.conversacion_dir != ruta:
            return
        if not parece_pausa(contenido):
            return

        restantes = self._auto_restantes.get(ruta, 0)
        if self.auto_continuar.get() and restantes > 0:
            self._auto_restantes[ruta] = restantes - 1
            self._aviso_transcripcion(
                f"↻ Sigo automáticamente (quedan {restantes - 1} de {self.MAX_AUTO})")
            self.after(400, self.continuar_turno)
            return

        if self.auto_continuar.get():
            self._aviso_transcripcion(
                f"⏸ Llegué al tope de {self.MAX_AUTO} continuaciones automáticas. "
                f"Seguí vos si querés.")
        self.boton_seguir.pack(fill="x", pady=(6, 0))

    def _cerrar_stream(self, ruta, contenido, hubo_error):
        """Reemplaza el texto que llegó en vivo por el Markdown renderizado.

        Durante el streaming se ve texto plano (es lo único que se puede pintar
        token a token). Al terminar se borra ese tramo y se vuelve a escribir
        todo lo que dijo el agente en este turno, ya con tablas, código y
        botones — incluidos los mensajes intermedios de las vueltas de
        herramientas, para que lo que ves coincida con lo que quedó guardado."""
        marca = self._marca_stream.pop(ruta, None)
        desde = self._marca_mensajes.pop(ruta, None)

        self.transcripcion.configure(state="normal")
        if marca is not None:
            self.transcripcion.delete(marca, "end")
        self.transcripcion.configure(state="disabled")

        if hubo_error or desde is None or self.conversacion is None:
            self._agregar_mensaje("", contenido, "rol_agente")
            return

        textos = [m.get("content") for m in self.conversacion.mensajes[desde:]
                  if m.get("role") == "assistant" and (m.get("content") or "").strip()]
        for i, texto in enumerate(textos or [contenido]):
            self._agregar_mensaje("" if i == 0 else "Agente", texto, "rol_agente")

    def _worker_chat(self, conversacion, texto: str):
        ruta = conversacion.conversacion_dir
        # Los hilos no pueden tocar widgets: los fragmentos van por la cola.
        al_fragmento = lambda f: self.cola.put(("chat_delta", (ruta, f)))
        try:
            respuesta = conversacion.enviar(texto, al_fragmento=al_fragmento)
            self.cola.put(("chat_ok", (ruta, respuesta)))
        except Exception:
            self.cola.put(("chat_error", (ruta, traceback.format_exc())))

    # -- pestaña proyectos -------------------------------------------------

    def _construir_tab_proyectos(self):
        panel = ttk.PanedWindow(self.tab_proyectos, orient="horizontal")
        panel.pack(fill="both", expand=True, pady=8)

        izquierda = ttk.Frame(panel, width=300)
        panel.add(izquierda, weight=0)

        ttk.Label(izquierda, text="Proyectos", font=(FUENTE_UI, 12, "bold")).pack(anchor="w")
        self.lista_proyectos = tk.Listbox(izquierda, font=(FUENTE_UI, 12), activestyle="none",
                                          relief="solid", borderwidth=1, highlightthickness=0,
                                          exportselection=False)
        self.lista_proyectos.pack(fill="both", expand=True, pady=(6, 8))
        self.lista_proyectos.bind("<<ListboxSelect>>", self._al_elegir_proyecto)

        botones = ttk.Frame(izquierda)
        botones.pack(fill="x")
        ttk.Button(botones, text="＋ Nuevo", command=self.nuevo_proyecto).pack(side="left")
        ttk.Button(botones, text="↻", width=3, command=self.refrescar_proyectos).pack(side="left", padx=6)
        ttk.Button(botones, text="📂", width=3, command=self.abrir_proyecto).pack(side="left")
        ttk.Button(botones, text="🗑", width=3, command=self.borrar_proyecto).pack(side="left", padx=6)

        derecha = ttk.Frame(panel)
        panel.add(derecha, weight=1)

        self.detalle_proyecto = ttk.Label(derecha, text="Elegí un proyecto o creá uno nuevo.",
                                          foreground=COLOR_TENUE, wraplength=620, justify="left")
        self.detalle_proyecto.pack(anchor="w", pady=(0, 8))

        acciones = ttk.Frame(derecha)
        acciones.pack(fill="x", pady=(0, 8))
        self.boton_ejecutar = ttk.Button(acciones, text="▶ Ejecutar / retomar",
                                         command=self.ejecutar_proyecto, state="disabled")
        self.boton_ejecutar.pack(side="left")
        self.boton_detener = ttk.Button(acciones, text="⏹ Detener", command=self.detener_proyecto,
                                        state="disabled")
        self.boton_detener.pack(side="left", padx=6)
        self.boton_publicar = ttk.Button(acciones, text="📚 Publicar como herramienta",
                                         command=self.publicar_proyecto, state="disabled")
        self.boton_publicar.pack(side="left", padx=(0, 6))
        ttk.Label(acciones, text="iteraciones extra si ya llegó al límite:").pack(side="left", padx=(16, 6))
        self.extender = tk.StringVar(value="5")
        ttk.Spinbox(acciones, from_=1, to=100, width=5, textvariable=self.extender).pack(side="left")

        contenedor, self.log_proyecto = self._texto_scroll(derecha, font=(FUENTE_MONO, 11), state="disabled")
        contenedor.pack(fill="both", expand=True)
        self.log_proyecto.tag_configure("normal", foreground=COLOR_TEXTO)
        self.log_proyecto.tag_configure("divisoria", foreground=COLOR_TENUE,
                                        justify="center", spacing1=8, spacing3=8)

    def refrescar_proyectos(self):
        self.proyectos = self.gestor_proyectos.listar_proyectos()
        self.lista_proyectos.delete(0, "end")
        for p in self.proyectos:
            estado = "✅" if p["exito"] else "🔄"
            self.lista_proyectos.insert("end", f" {estado} {p['nombre']}")

    def _proyecto_seleccionado(self):
        seleccion = self.lista_proyectos.curselection()
        return self.proyectos[seleccion[0]] if seleccion else None

    def _al_elegir_proyecto(self, _evento):
        p = self._proyecto_seleccionado()
        if not p:
            return
        self.detalle_proyecto.configure(
            text=f"{p['descripcion']}\n"
                 f"Iteraciones: {p['iteraciones']} · Mejor puntaje: {formato.percent(p['mejor_puntaje'])} · "
                 f"Actualizado: {p['actualizado']}\n{p['path']}"
        )
        if not self.ocupado_proyecto:
            self.boton_ejecutar.configure(state="normal")
            hay_codigo = (p["path"] / "solucion.py").exists() or \
                         (p["path"] / "ultima_version.py").exists()
            self.boton_publicar.configure(state="normal" if hay_codigo else "disabled")

    def nuevo_proyecto(self):
        dialogo = DialogoNuevoProyecto(self)
        if not dialogo.resultado:
            return
        descripcion, umbral, max_iter = dialogo.resultado
        self.gestor_proyectos.crear_proyecto(
            descripcion=descripcion,
            objetivos=OBJETIVOS_DEFAULT,
            umbral_global=umbral,
            max_iteraciones=max_iter,
        )
        self.refrescar_proyectos()
        self.lista_proyectos.selection_clear(0, "end")
        self.lista_proyectos.selection_set(0)
        self._al_elegir_proyecto(None)

    def abrir_proyecto(self):
        p = self._proyecto_seleccionado()
        plataforma.abrir_carpeta(p["path"] if p else AGENT_CODE_DIR)

    def borrar_proyecto(self):
        p = self._proyecto_seleccionado()
        if not p:
            messagebox.showinfo("Borrar proyecto", "Elegí primero un proyecto de la lista.")
            return
        if self.ocupado_proyecto:
            messagebox.showwarning("Borrar proyecto", "Hay un proyecto corriendo. Detenelo primero.")
            return

        if not messagebox.askokcancel(
            "Borrar proyecto",
            f"«{p['nombre']}»\n\n"
            f"{p['iteraciones']} iteración(es) · mejor puntaje {formato.percent(p['mejor_puntaje'])} · "
            f"{_tamano_legible(p['path'])}\n"
            f"{p['path']}\n\n"
            "Se borran las iteraciones, la memoria y la solución de ese proyecto.\n"
            "Los módulos que haya publicado en la biblioteca compartida NO se tocan.\n"
            "Va a la Papelera, así que se puede recuperar."
        ):
            return

        try:
            destino = _mover_a_papelera(p["path"])
        except Exception as e:
            messagebox.showerror("Borrar proyecto", f"No pude borrarlo:\n{e}")
            return

        self._log(f"🗑 Proyecto «{p['nombre']}» movido a la Papelera: {destino}")
        self.detalle_proyecto.configure(text="Elegí un proyecto o creá uno nuevo.")
        self.boton_ejecutar.configure(state="disabled")
        self.refrescar_proyectos()

    def publicar_proyecto(self):
        """Manda el código de un proyecto a la biblioteca compartida, que es como
        el proyecto 'suma una herramienta': a partir de acá cualquier conversación
        puede leerla y ejecutarla con ejecutar_modulo_biblioteca."""
        p = self._proyecto_seleccionado()
        if not p:
            return

        origen = p["path"] / "solucion.py"
        if not origen.exists():
            origen = p["path"] / "ultima_version.py"
        if not origen.exists():
            messagebox.showinfo("Publicar herramienta",
                                "Este proyecto todavía no generó código para publicar.")
            return

        if origen.name == "ultima_version.py" and not messagebox.askokcancel(
            "Publicar herramienta",
            "Este proyecto nunca alcanzó su umbral, así que se publicaría "
            f"«{origen.name}» (la última versión generada).\n\n¿Publicarla igual?"
        ):
            return

        nombre = simpledialog.askstring(
            "Publicar como herramienta",
            "Nombre de la herramienta (así la va a buscar el agente):",
            initialvalue=p["nombre"].replace("-", "_"), parent=self)
        if not nombre or not nombre.strip():
            return

        descripcion = simpledialog.askstring(
            "Publicar como herramienta",
            "¿Qué hace y cómo se usa? (nombrá las funciones y sus parámetros —\n"
            "esto es lo único que el agente ve antes de decidir usarla)",
            initialvalue=p["descripcion"][:200], parent=self)
        if descripcion is None:
            return

        resultado = self.biblioteca.publicar(
            nombre=nombre.strip(),
            descripcion=descripcion.strip() or p["descripcion"],
            contenido=origen.read_text(encoding='utf-8'),
        )
        if "error" in resultado:
            messagebox.showerror("Publicar herramienta", resultado["error"])
            return

        accion = "actualizada" if "actualizado" in resultado else "publicada"
        self._log(f"📚 Herramienta «{nombre.strip()}» {accion} en la biblioteca "
                  f"(desde {origen.name})")
        self.refrescar_biblioteca()
        messagebox.showinfo(
            "Publicar herramienta",
            f"«{nombre.strip()}» {accion}. Ya está disponible para el agente.")

    def ejecutar_proyecto(self):
        if self.ocupado_proyecto:
            return
        p = self._proyecto_seleccionado()
        if not p:
            return
        try:
            extender = max(1, int(self.extender.get()))
        except ValueError:
            extender = 5

        self.ocupado_proyecto = True
        self.boton_ejecutar.configure(state="disabled")
        self.boton_publicar.configure(state="disabled")
        self.boton_detener.configure(state="normal")

        # No se limpia entre corridas: se acumulan separadas por una divisoria
        # con el timestamp de arranque.
        self.log_proyecto.configure(state="normal")
        if self.log_proyecto.index("end-1c") != "1.0":
            self.log_proyecto.insert("end", f"\n{'─' * 16}   {'─' * 16}\n", "divisoria")
        self.log_proyecto.insert("end", f"▶ corrida · {time.strftime('%Y-%m-%d %H:%M:%S')}\n", "divisoria")
        self.log_proyecto.configure(state="disabled")

        threading.Thread(target=self._worker_proyecto, args=(p["path"], extender), daemon=True).start()

    def _worker_proyecto(self, proyecto_dir: Path, extender: int):
        try:
            agente = AgenteInteractivo(proyecto_dir)
            # En la UI no hay dónde hacer input(): el loop corre sin las
            # pausas interactivas de consola, y se corta con «Detener».
            agente.interaccion_activa = False
            self.agente_actual = agente

            if not agente.objetivos_alcanzados and agente.iteracion >= agente.max_iteraciones:
                agente.max_iteraciones += extender
                print(f"🔼 Límite extendido a {agente.max_iteraciones} iteraciones")

            resultado = agente.ejecutar()
            agente.limpiar()
            self.cola.put(("proyecto_ok", resultado))
        except Exception:
            self.cola.put(("proyecto_error", traceback.format_exc()))
        finally:
            self.agente_actual = None

    def detener_proyecto(self):
        agente = self.agente_actual
        if agente is None:
            return
        # El loop es `while ... and self.iteracion < self.max_iteraciones`:
        # bajando el techo, corta al terminar la iteración en curso.
        agente.max_iteraciones = agente.iteracion
        self._log("⏹ Detención pedida: corta al terminar la iteración en curso…")
        self.boton_detener.configure(state="disabled")

    # -- pestaña biblioteca ------------------------------------------------

    def _construir_tab_biblioteca(self):
        panel = ttk.PanedWindow(self.tab_biblioteca, orient="horizontal")
        panel.pack(fill="both", expand=True, pady=8)

        izquierda = ttk.Frame(panel, width=260)
        panel.add(izquierda, weight=0)

        ttk.Label(izquierda, text="Módulos compartidos", font=(FUENTE_UI, 12, "bold")).pack(anchor="w")
        self.lista_modulos = tk.Listbox(izquierda, font=(FUENTE_UI, 12), activestyle="none",
                                        relief="solid", borderwidth=1, highlightthickness=0,
                                        exportselection=False)
        self.lista_modulos.pack(fill="both", expand=True, pady=(6, 8))
        self.lista_modulos.bind("<<ListboxSelect>>", self._al_elegir_modulo)

        botones = ttk.Frame(izquierda)
        botones.pack(fill="x")
        ttk.Button(botones, text="↻", width=3, command=self.refrescar_biblioteca).pack(side="left")
        ttk.Button(botones, text="📂", width=3, command=self.abrir_biblioteca).pack(side="left", padx=6)
        ttk.Button(botones, text="🗑", width=3, command=self.borrar_modulo).pack(side="left")

        derecha = ttk.Frame(panel)
        panel.add(derecha, weight=1)
        contenedor, self.vista_modulo = self._texto_scroll(derecha, font=(FUENTE_MONO, 11), state="disabled")
        contenedor.pack(fill="both", expand=True)

    def refrescar_biblioteca(self):
        self.modulos = self.biblioteca.listar().get("modulos", [])
        self.lista_modulos.delete(0, "end")
        for m in self.modulos:
            self.lista_modulos.insert("end", f" {m['nombre']}")

    def abrir_biblioteca(self):
        plataforma.abrir_carpeta(self.biblioteca.base_dir)

    def borrar_modulo(self):
        seleccion = self.lista_modulos.curselection()
        if not seleccion:
            messagebox.showinfo("Borrar módulo", "Elegí primero un módulo de la lista.")
            return
        if self.ocupado_chat or self.ocupado_proyecto:
            messagebox.showwarning(
                "Borrar módulo",
                "Hay trabajo en curso que podría estar usando la biblioteca. "
                "Esperá a que termine."
            )
            return

        m = self.modulos[seleccion[0]]
        if not messagebox.askokcancel(
            "Borrar módulo de la biblioteca",
            f"«{m['nombre']}»\n\n{m.get('descripcion', '')}\n\n"
            "Deja de estar disponible para TODOS los proyectos y conversaciones.\n"
            "El archivo va a la Papelera, así que se puede recuperar."
        ):
            return

        resultado = self.biblioteca.borrar_modulo(m["nombre"])
        if "error" in resultado:
            messagebox.showerror("Borrar módulo", resultado["error"])
            return

        ruta = resultado.get("archivo")
        if ruta:
            try:
                destino = _mover_a_papelera(Path(ruta))
                self._log(f"🗑 Módulo «{m['nombre']}» movido a la Papelera: {destino}")
            except Exception as e:
                self._log(f"⚠️ Saqué «{m['nombre']}» del registro, pero no pude mover "
                          f"el archivo a la Papelera: {e}")
        else:
            self._log(f"🗑 Módulo «{m['nombre']}» sacado de la biblioteca")

        self.vista_modulo.configure(state="normal")
        self.vista_modulo.delete("1.0", "end")
        self.vista_modulo.configure(state="disabled")
        self.refrescar_biblioteca()

    def _al_elegir_modulo(self, _evento):
        seleccion = self.lista_modulos.curselection()
        if not seleccion:
            return
        resultado = self.biblioteca.leer_modulo(self.modulos[seleccion[0]]["nombre"])
        self.vista_modulo.configure(state="normal")
        self.vista_modulo.delete("1.0", "end")
        if "error" in resultado:
            self.vista_modulo.insert("end", resultado["error"])
        else:
            deps = ", ".join(resultado.get("dependencias") or []) or "—"
            self.vista_modulo.insert(
                "end",
                f"# {resultado['nombre']}\n# {resultado['descripcion']}\n"
                f"# depende de: {deps}\n\n{resultado['contenido']}"
            )
        self.vista_modulo.configure(state="disabled")

    # -- pestaña procesador ------------------------------------------------

    def _construir_tab_procesador(self):
        cont = ttk.Frame(self.tab_procesador)
        cont.pack(fill="both", expand=True, pady=8)

        ttk.Label(cont, text="Pegá una consulta larga y mirá cómo la parte",
                  font=(FUENTE_UI, 12, "bold")).pack(anchor="w")
        ttk.Label(cont,
                  text="Corta por conectores lógicos (y, para, usando, cuando, dado que…) en "
                       "sub-preguntas independientes, y las manda a la IA en paralelo.",
                  foreground=COLOR_TENUE, wraplength=820, justify="left").pack(anchor="w", pady=(2, 8))

        self.entrada_proc = tk.Text(cont, height=6, wrap="word", font=(FUENTE_UI, 12),
                                    relief="solid", borderwidth=1, padx=10, pady=8)
        self.entrada_proc.pack(fill="x")

        barra = ttk.Frame(cont)
        barra.pack(fill="x", pady=(8, 8))

        ttk.Label(barra, text="Modelo:").pack(side="left")
        self.modo_proc = tk.StringVar(value="deepseek")
        ttk.Combobox(barra, textvariable=self.modo_proc, width=11, state="readonly",
                     values=("deepseek", "claude", "qwen", "gemini", "combinado", "comparar")
                     ).pack(side="left", padx=(6, 4))
        ttk.Label(barra, text="combinado = reparte · comparar = los dos por pregunta",
                  foreground=COLOR_TENUE).pack(side="left", padx=(2, 16))

        ttk.Label(barra, text="Mínimo:").pack(side="left")
        self.minimo_proc = tk.StringVar(value="6")
        ttk.Spinbox(barra, from_=2, to=30, width=4,
                    textvariable=self.minimo_proc).pack(side="left", padx=(6, 16))

        self.boton_descomponer = ttk.Button(barra, text="🧩 Solo descomponer",
                                            command=lambda: self.correr_procesador(True))
        self.boton_descomponer.pack(side="left")
        self.boton_procesar = ttk.Button(barra, text="⚡ Descomponer y responder",
                                         command=lambda: self.correr_procesador(False))
        self.boton_procesar.pack(side="left", padx=6)

        contenedor, self.salida_proc = self._texto_scroll(cont, font=(FUENTE_MONO, 11),
                                                          state="disabled")
        contenedor.pack(fill="both", expand=True)
        self.salida_proc.tag_configure("titulo", foreground=COLOR_USUARIO,
                                       font=(FUENTE_MONO, 11, "bold"))
        self.salida_proc.tag_configure("meta", foreground=COLOR_TENUE)
        self.salida_proc.tag_configure("err", foreground=COLOR_ERROR)
        self.salida_proc.tag_configure("divisoria", foreground=COLOR_TENUE,
                                       justify="center", spacing1=8, spacing3=8)

        self.estado_proc = ttk.Label(cont, text="", foreground=COLOR_TENUE)
        self.estado_proc.pack(anchor="w", pady=(4, 0))

    def _escribir_proc(self, texto: str, tag: str = ""):
        self.salida_proc.configure(state="normal")
        self.salida_proc.insert("end", texto, tag)
        self.salida_proc.configure(state="disabled")
        self.salida_proc.see("end")

    def correr_procesador(self, solo_descomponer: bool):
        if self.ocupado_procesador:
            return
        texto = self.entrada_proc.get("1.0", "end").strip()
        if not texto:
            messagebox.showinfo("Procesador", "Pegá primero el texto a descomponer.")
            return
        try:
            minimo = max(2, int(self.minimo_proc.get()))
        except ValueError:
            minimo = 6

        # No se limpia entre corridas: se acumulan separadas por una divisoria,
        # y cada corrida arranca con su timestamp.
        self.salida_proc.configure(state="normal")
        if self.salida_proc.index("end-1c") != "1.0":
            self.salida_proc.insert("end", f"\n{'─' * 16}   {'─' * 16}\n", "divisoria")
        self.salida_proc.insert("end", f"▶ corrida · {time.strftime('%H:%M:%S')}\n\n", "meta")
        self.salida_proc.configure(state="disabled")

        self.ocupado_procesador = True
        self.boton_descomponer.configure(state="disabled")
        self.boton_procesar.configure(state="disabled")
        self.estado_proc.configure(
            text="⏳ Descomponiendo…" if solo_descomponer else "⏳ Descomponiendo y consultando en paralelo…")

        threading.Thread(target=self._worker_procesador,
                         args=(texto, self.modo_proc.get(), minimo, solo_descomponer),
                         daemon=True).start()

    def _worker_procesador(self, texto, modo, minimo, solo_descomponer):
        try:
            resultado = procesar(texto, modo=modo, minimo=minimo,
                                 solo_descomponer=solo_descomponer)
            self.cola.put(("proc_ok", resultado))
        except ErrorProcesador as e:
            self.cola.put(("proc_error", str(e)))
        except Exception:
            self.cola.put(("proc_error", traceback.format_exc()))

    def _mostrar_resultado_procesador(self, r):
        self._escribir_proc(f"{len(r.preguntas)} sub-preguntas\n", "titulo")
        for i, p in enumerate(r.preguntas, 1):
            self._escribir_proc(f"  {i}. {p}\n")

        if not r.respuestas:
            self._escribir_proc(f"\n({formato.duration(r.segundos_total)} · sin consultar a la IA)\n", "meta")
            return

        secuencial = sum(x.segundos for x in r.respuestas)
        self._escribir_proc(
            f"\n{len(r.respuestas)} consultas en paralelo · modo '{r.modo}' · "
            f"{formato.duration(r.segundos_total)} reales vs {formato.duration(secuencial)} "
            f"si fuera en serie\n\n", "meta")

        for i, x in enumerate(r.respuestas, 1):
            self._escribir_proc(f"{i}. {x.pregunta}\n", "titulo")
            self._escribir_proc(f"   [{x.proveedor} · {formato.duration(x.segundos)}]\n", "meta")
            self._escribir_proc(f"   {x.error or x.texto}\n\n", "err" if x.error else "")

        if r.fallidas:
            self._escribir_proc(f"⚠️ {len(r.fallidas)} sub-pregunta(s) fallaron.\n", "err")

    def _fin_procesador(self):
        self.ocupado_procesador = False

        # Las operaciones destructivas las decide el usuario en la UI. El hilo
        # de trabajo se bloquea en un Event hasta que el diálogo responde.
        ejecucion.registrar_confirmador(self._confirmar_desde_hilo)
        self.boton_descomponer.configure(state="normal")
        self.boton_procesar.configure(state="normal")
        self.estado_proc.configure(text="")


    # -- pestaña configuración ---------------------------------------------

    def _construir_tab_config(self):
        cont = ttk.Frame(self.tab_config)
        cont.pack(fill="both", expand=True, pady=8, padx=4)

        ttk.Label(cont, text="Claves y rutas", font=(FUENTE_UI, 13, "bold")).pack(anchor="w")
        ttk.Label(cont, text=f"Se guardan en {rutas.ruta_env()} — con tus datos, no con el código, "
                             f"así compartir la app nunca se las lleva.",
                  foreground=COLOR_TENUE, wraplength=880, justify="left").pack(anchor="w", pady=(2, 10))

        self.campos_config = {}
        grilla = ttk.Frame(cont)
        grilla.pack(fill="x")
        valores = configuracion.leer()

        for fila, (clave, etiqueta, secreto) in enumerate(configuracion.CAMPOS):
            ttk.Label(grilla, text=etiqueta).grid(row=fila, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=valores.get(clave, ""))
            entrada = ttk.Entry(grilla, textvariable=var, width=54,
                                show="•" if secreto else "")
            entrada.grid(row=fila, column=1, sticky="w", padx=(10, 6))
            self.campos_config[clave] = (var, entrada, secreto)

            if secreto:
                ttk.Button(grilla, text="👁", width=3,
                           command=lambda e=entrada: e.configure(
                               show="" if e.cget("show") else "•")).grid(row=fila, column=2)
                proveedor = ("deepseek" if clave.startswith("DEEPSEEK")
                             else "qwen" if clave.startswith("QWEN")
                             else "gemini" if clave.startswith("GEMINI") else "claude")
                ttk.Button(grilla, text="Probar", width=8,
                           command=lambda p=proveedor: self.probar_proveedor(p)
                           ).grid(row=fila, column=3, padx=6)

        acciones = ttk.Frame(cont)
        acciones.pack(fill="x", pady=(14, 8))
        ttk.Button(acciones, text="💾 Guardar", command=self.guardar_config).pack(side="left")
        ttk.Button(acciones, text="🔄 Diagnóstico", command=self.refrescar_diagnostico).pack(side="left", padx=6)
        ttk.Button(acciones, text="💲 Actualizar precios",
                   command=self.actualizar_precios).pack(side="left", padx=(0, 6))
        # El .dmg lo arma ditto/hdiutil: solo existe en macOS. En el resto se
        # ofrece el zip portable, que ya estaba escrito pero no tenia boton.
        if plataforma.ES_MAC:
            ttk.Button(acciones, text="🐋 Crear instalador (.dmg)",
                       command=self.crear_instalador).pack(side="left")
        else:
            ttk.Button(acciones, text="📦 Crear paquete portable (.zip)",
                       command=self.crear_zip_portable).pack(side="left")
        ttk.Button(acciones, text="📂 Abrir carpeta de datos",
                   command=lambda: plataforma.abrir_carpeta(rutas.dir_datos())).pack(side="left", padx=6)

        contenedor, self.salida_config = self._texto_scroll(cont, font=(FUENTE_MONO, 11), state="disabled")
        contenedor.pack(fill="both", expand=True)
        self.salida_config.tag_configure("ok", foreground=COLOR_AGENTE)
        self.salida_config.tag_configure("mal", foreground=COLOR_ERROR)
        self.salida_config.tag_configure("tenue", foreground=COLOR_TENUE)

    def _escribir_config(self, texto, tag=""):
        self.salida_config.configure(state="normal")
        self.salida_config.insert("end", texto, tag)
        self.salida_config.configure(state="disabled")
        self.salida_config.see("end")

    def guardar_config(self):
        cambios = {c: var.get().strip() for c, (var, _, _) in self.campos_config.items()}
        resultado = configuracion.guardar(cambios)
        if "error" in resultado:
            messagebox.showerror("Configuración", resultado["error"])
            return
        self._escribir_config(f"💾 Guardado en {resultado['guardado']}\n", "ok")
        self._escribir_config("   Los cambios ya valen para las conversaciones nuevas.\n"
                              "   Las que estén abiertas usan el proveedor con el que arrancaron.\n", "tenue")
        self.refrescar_diagnostico()

    def probar_proveedor(self, proveedor):
        """Guarda primero y hace UNA llamada real: construir el cliente no prueba nada."""
        self.guardar_config()
        self._escribir_config(f"\n⏳ Probando {proveedor}…\n", "tenue")
        self.update_idletasks()

        def trabajo():
            self.cola.put(("config_prueba", (proveedor, configuracion.probar(proveedor))))
        threading.Thread(target=trabajo, daemon=True).start()

    def refrescar_diagnostico(self):
        d = rutas.diagnostico()
        self.salida_config.configure(state="normal")
        self.salida_config.delete("1.0", "end")
        self.salida_config.configure(state="disabled")

        self._escribir_config("DIAGNÓSTICO\n", "ok")
        self._escribir_config(f"  Sistema        {plataforma.descripcion()}\n")
        marca = lambda ok: "✅" if ok else "❌"
        self._escribir_config(f"  Datos          {d['datos']}\n")
        self._escribir_config(f"                 {marca(d['datos_existe'])} existe   "
                              f"{marca(d['datos_escribible'])} escribible\n")
        self._escribir_config(f"  Código         {d['app']}  {marca(d['app_existe'])}\n")
        self._escribir_config(f"  Configuración  {d['env']}  {marca(d['env_existe'])}\n")

        # El orden importa: en Windows chmod(0o600) "funciona" pero el archivo
        # reporta 0o666, así que preguntar primero por el modo daba una alarma
        # falsa ("otros pueden leer tus claves") en cada arranque.
        if not plataforma.soporta_permisos_posix():
            self._escribir_config("                 ℹ️ en Windows el acceso va por ACLs, "
                                  "no por permisos POSIX\n")
        elif d["env_permisos"] and d["env_permisos"] != "0o600":
            self._escribir_config(f"                 ⚠️ permisos {d['env_permisos']}: "
                                  f"otros usuarios del equipo pueden leer tus claves\n", "mal")

        faltan = [s for s, ok in d["subcarpetas"].items() if not ok]
        self._escribir_config(f"  Subcarpetas    {'todas ✅' if not faltan else '❌ faltan: ' + ', '.join(faltan)}\n")

        vals = configuracion.leer()
        self._escribir_config("\n  Claves cargadas\n")
        for clave in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
            v = vals.get(clave, "")
            self._escribir_config(f"    {clave:20} {configuracion.enmascarar(v) if v else '— sin configurar'}\n",
                                  "" if v else "tenue")
        self._escribir_config("\n  Usá «Probar» para confirmar que la API las acepta.\n", "tenue")

    def actualizar_precios(self):
        """Relee las páginas de precios y PROPONE los cambios.

        Nunca guarda sola: no hay API oficial de precios en ninguno de los dos
        proveedores, así que esto es parsear documentación, y un número mal
        leído acá se traduce en plata mal calculada. La primera versión de este
        parser tomó la tabla de Batch de Anthropic y devolvió todo a mitad de
        precio — por eso decidís vos."""
        from core import precios as tabla
        self._escribir_config("\n⏳ Leyendo las páginas oficiales…\n", "tenue")
        self.update_idletasks()

        def trabajo():
            self.cola.put(("precios", tabla.fetch_from_web()))
        threading.Thread(target=trabajo, daemon=True).start()

    def _mostrar_precios(self, resultado):
        from core import precios as tabla
        for e in resultado["errores"]:
            self._escribir_config(f"⚠️ {e}\n", "mal")

        encontrados = resultado["encontrados"]
        if not encontrados:
            self._escribir_config("No pude leer ningún precio. Los que tenés siguen igual.\n", "mal")
            return

        actuales = configuracion.precios_actuales()
        d = tabla.compare(encontrados, actuales)

        self._escribir_config(f"\nLeí {len(encontrados)} modelos de:\n", "ok")
        for nombre, url in resultado["fuentes"].items():
            self._escribir_config(f"   {url}\n", "tenue")

        if not d["cambios"] and not d["agregados"]:
            self._escribir_config("\n✅ Todo coincide con lo que ya tenés cargado.\n", "ok")
            return

        if d["cambios"]:
            self._escribir_config(f"\nCambian {len(d['cambios'])}:\n")
            for modelo, viejo, nuevo in d["cambios"]:
                self._escribir_config(
                    f"   {modelo:22} {viejo[0]}/{viejo[1]}  →  {nuevo[0]}/{nuevo[1]}\n", "mal")
        if d["agregados"]:
            self._escribir_config(f"\nModelos nuevos ({len(d['agregados'])}):\n")
            for modelo, entrada, salida in d["agregados"]:
                self._escribir_config(f"   {modelo:22} {entrada}/{salida}\n")

        if not messagebox.askokcancel(
                "Actualizar precios",
                f"Leí las páginas oficiales y encontré {len(d['cambios'])} precio(s) "
                f"distinto(s) y {len(d['agregados'])} modelo(s) nuevo(s).\n\n"
                "Revisá el detalle en la ventana antes de aceptar: esto se parsea de "
                "documentación, no de una API, así que puede leer mal.\n\n"
                "¿Aplico estos precios?"):
            self._escribir_config("\nNo apliqué nada.\n", "tenue")
            return

        configuracion.guardar(tabla.as_env(encontrados))
        self._escribir_config(f"\n💲 Precios actualizados ({len(encontrados)} modelos).\n", "ok")
        self.refrescar_conversaciones()

    def crear_instalador(self):
        """Arma el .app con el código adentro y lo mete en un .dmg.

        Es la forma en que se distribuye una app de Mac: el que lo recibe abre
        el disco, arrastra la ballena a Aplicaciones y listo — un icono, no una
        carpeta con archivos sueltos."""
        from tkinter import filedialog
        destino = filedialog.asksaveasfilename(
            parent=self, title="Guardar el instalador",
            defaultextension=".dmg", initialfile="AgentFactory.dmg")
        if not destino:
            return

        destino = Path(destino)
        self._escribir_config("\n⏳ Armando la app…\n", "tenue")
        self.update_idletasks()

        app = destino.parent / "AgentFactory.app"
        r = empaquetar.construir_app(app)
        if "error" in r:
            self._escribir_config(f"❌ {r['error']}\n", "mal")
            for f in r.get("faltan", []):
                self._escribir_config(f"   falta: {f}\n", "mal")
            for h in r.get("hallazgos", []):
                self._escribir_config(f"   {h['archivo']}:{h['linea']} — {h['que']}\n", "mal")
            messagebox.showerror("Crear instalador", r["error"])
            return

        self._escribir_config(
            f"   app: {r['archivos']} archivos · {formato.size(r['bytes'])} · v{r['version']}\n", "tenue")
        self.update_idletasks()

        d = empaquetar.construir_dmg(destino, app)
        if "error" in d:
            self._escribir_config(f"❌ {d['error']}\n", "mal")
            messagebox.showerror("Crear instalador", d["error"])
            return

        self._escribir_config(f"\n🐋 {d['dmg']}\n", "ok")
        self._escribir_config(f"   {formato.size(d['bytes'])}\n")
        self._escribir_config("   Del otro lado: abrir el .dmg, arrastrar la ballena a "
                              "Aplicaciones, doble clic.\n", "tenue")
        self._escribir_config("   La primera vez abre una Terminal mostrando la "
                              "preparación del entorno (~24 MB, menos de un minuto).\n", "tenue")
        self._escribir_config("   macOS va a pedir confirmación por ser una app sin firmar: "
                              "clic derecho → Abrir.\n", "tenue")

    def crear_zip_portable(self):
        from tkinter import filedialog
        destino = filedialog.asksaveasfilename(
            parent=self, title="Guardar el paquete portable",
            defaultextension=".zip", initialfile="AgentFactory-portable.zip")
        if not destino:
            return

        resultado = empaquetar.crear_zip(Path(destino))
        if "error" in resultado:
            self._escribir_config(f"\n❌ {resultado['error']}\n", "mal")
            for h in resultado.get("hallazgos", []):
                self._escribir_config(f"   {h['archivo']}:{h['linea']} — {h['que']}\n", "mal")
            messagebox.showerror("Crear paquete", resultado["error"])
            return

        self._escribir_config(f"\n📦 {resultado['zip']}\n", "ok")
        self._escribir_config(f"   {resultado['archivos']} archivos · "
                              f"{formato.size(resultado['bytes'])}\n")
        self._escribir_config("   Sin .env, sin venv, sin tus conversaciones. "
                              "Revisado contra credenciales.\n", "tenue")
        self._escribir_config("   En la otra Mac: clic derecho sobre INICIAR.command → Abrir "
                              "(la primera vez).\n", "tenue")


    # -- pestaña proxy -----------------------------------------------------

    def _construir_tab_proxy(self):
        cont = ttk.Frame(self.tab_proxy)
        cont.pack(fill="both", expand=True, pady=6, padx=4)

        barra = ttk.Frame(cont)
        barra.pack(fill="x", pady=(0, 6))
        self.boton_proxy = ttk.Button(barra, text="▶ Iniciar proxy", command=self.alternar_proxy)
        self.boton_proxy.pack(side="left")
        ttk.Label(barra, text="Puerto:").pack(side="left", padx=(10, 2))
        self.puerto_proxy = tk.StringVar(value=str(proxymod.PUERTO_DEFAULT))
        ttk.Spinbox(barra, from_=1024, to=65535, width=7,
                    textvariable=self.puerto_proxy).pack(side="left")
        # Siempre habilitado: si el proxy no está corriendo, el propio botón lo
        # arranca antes de abrir Firefox (antes quedaba gris y el clic no hacía
        # nada, que se leía como "no abre Firefox").
        self.boton_firefox = ttk.Button(barra, text="🦊 Abrir Firefox",
                                        command=self.abrir_firefox_proxy)
        self.boton_firefox.pack(side="left", padx=8)
        ttk.Button(barra, text="🗑 Limpiar", command=self.limpiar_proxy).pack(side="left")
        ttk.Button(barra, text="🔊 Reactivar ignoradas",
                   command=self.reactivar_ignoradas).pack(side="left", padx=(4, 0))
        self.estado_proxy = ttk.Label(barra, text="detenido", foreground=COLOR_TENUE)
        self.estado_proxy.pack(side="right")

        panel = ttk.PanedWindow(cont, orient="horizontal")
        panel.pack(fill="both", expand=True)

        izq = ttk.Frame(panel, width=340)
        panel.add(izq, weight=0)

        filtro_bar = ttk.Frame(izq)
        filtro_bar.pack(fill="x", pady=(0, 4))
        ttk.Label(filtro_bar, text="Filtro:").pack(side="left")
        self.filtro_proxy = tk.StringVar()
        ent = ttk.Entry(filtro_bar, textvariable=self.filtro_proxy, width=16)
        ent.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ent.bind("<KeyRelease>", lambda _e: self.refrescar_arbol_proxy())
        ttk.Button(filtro_bar, text="⊕", width=2,
                   command=lambda: self._expandir_arbol(True)).pack(side="left")
        ttk.Button(filtro_bar, text="⊖", width=2,
                   command=lambda: self._expandir_arbol(False)).pack(side="left")

        # Botones rápidos para ocultar los tipos que más ensucian.
        chips = ttk.Frame(izq)
        chips.pack(fill="x", pady=(0, 4))
        self.ocultar_estaticos = tk.BooleanVar(value=False)
        ttk.Checkbutton(chips, text="ocultar css/js/img/fonts",
                        variable=self.ocultar_estaticos,
                        command=self.refrescar_arbol_proxy).pack(side="left")

        # sub-frame propio con grid (izq ya usa pack para la barra de filtro).
        marco_arbol = ttk.Frame(izq)
        marco_arbol.pack(fill="both", expand=True)
        self.arbol_proxy = ttk.Treeview(marco_arbol, columns=("info",), show="tree headings",
                                        selectmode="extended")
        self.arbol_proxy.heading("#0", text="Sitio / ruta")
        self.arbol_proxy.heading("info", text="método · estado")
        # stretch=False + minwidth grande: la columna puede ser MÁS ancha que el
        # panel, y ahí entra el scroll horizontal para los paths largos. El
        # divisor del panel (arrastrable) agranda el árbol entero.
        self.arbol_proxy.column("#0", width=300, minwidth=140, stretch=False)
        self.arbol_proxy.column("info", width=110, minwidth=90, anchor="w", stretch=False)
        vs = ttk.Scrollbar(marco_arbol, orient="vertical", command=self.arbol_proxy.yview)
        hs = ttk.Scrollbar(marco_arbol, orient="horizontal", command=self.arbol_proxy.xview)
        self.arbol_proxy.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.arbol_proxy.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        marco_arbol.rowconfigure(0, weight=1)
        marco_arbol.columnconfigure(0, weight=1)

        # Doble clic en la cabecera "Sitio / ruta": ensancha la columna al path
        # más largo visible (autoajuste rápido cuando hay rutas muy largas).
        self.arbol_proxy.heading("#0", text="Sitio / ruta  (2× clic para ensanchar)",
                                 command=self._autoancho_arbol)
        self.arbol_proxy.bind("<<TreeviewSelect>>", self._al_elegir_flujo)
        self.arbol_proxy.bind("<BackSpace>", self._borrar_seleccion_proxy)
        self.arbol_proxy.bind("<Delete>", self._borrar_seleccion_proxy)
        # Button-3 es el clic derecho de verdad en Windows y Linux, y tambien
        # funciona en macOS. Los otros dos son las convenciones de macOS
        # (boton del medio / Control+clic) y se dejan por costumbre.
        self.arbol_proxy.bind("<Button-3>", self._menu_flujo)
        self.arbol_proxy.bind("<Button-2>", self._menu_flujo)
        self.arbol_proxy.bind("<Control-Button-1>", self._menu_flujo)
        # id de flujo por cada nodo del árbol
        self._flujo_de_nodo = {}

        der = ttk.Frame(panel)
        panel.add(der, weight=1)
        # Estilo ZAP: dos pestañas (Petición / Respuesta), y dentro de cada una
        # una caja para headers y otra para body, con divisor arrastrable.
        detalle = ttk.Notebook(der)
        detalle.pack(fill="both", expand=True)

        def _pestana(titulo):
            marco = ttk.Frame(detalle)
            detalle.add(marco, text=titulo)
            vp = ttk.PanedWindow(marco, orient="vertical")
            vp.pack(fill="both", expand=True)
            mh = ttk.Labelframe(vp, text="Headers")
            vp.add(mh, weight=1)
            _, cajah = self._texto_scroll(mh, font=(FUENTE_MONO, 10), state="disabled")
            cajah.master.pack(fill="both", expand=True)
            mb = ttk.Labelframe(vp, text="Body")
            vp.add(mb, weight=2)
            _, cajab = self._texto_scroll(mb, font=(FUENTE_MONO, 10), state="disabled")
            cajab.master.pack(fill="both", expand=True)
            return cajah, cajab

        self.proxy_req_h, self.proxy_req_b = _pestana("  Petición  ")
        self.proxy_resp_h, self.proxy_resp_b = _pestana("  Respuesta  ")
        self.proxy_detalle = detalle

        for t in (self.proxy_req_h, self.proxy_req_b, self.proxy_resp_h, self.proxy_resp_b):
            t.tag_configure("hdr", foreground=COLOR_TENUE, font=(FUENTE_MONO, 10))
            t.tag_configure("linea1", foreground=COLOR_USUARIO, font=(FUENTE_MONO, 10, "bold"))
            t.tag_configure("aviso", foreground=COLOR_ERROR)

        self._aviso_proxy_inicial()

    def _aviso_proxy_inicial(self):
        self._escribir_recuadro(self.proxy_req_h,
            "Iniciá el proxy y abrí Firefox para empezar a capturar.\n\n"
            "La primera vez el certificado se instala solo (o por mitm.it). Tu "
            "Firefox normal no se toca: se usa un perfil aparte.", "hdr")

    def _escribir_recuadro(self, widget, texto, tag=""):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", texto, tag)
        widget.configure(state="disabled")

    def alternar_proxy(self):
        if self.proxy and self.proxy.corriendo:
            self.proxy.detener()
            self.proxy = None
            self.boton_proxy.configure(text="▶ Iniciar proxy")
            self.estado_proxy.configure(text="detenido", foreground=COLOR_TENUE)
            return

        # mitmproxy es pesado y no viene en la instalación base: se ofrece
        # instalarlo la primera vez que se usa el proxy.
        if not self._mitmproxy_disponible():
            # Sin preguntar: loader instalando y, al terminar, reintenta solo.
            self._instalar_mitmproxy_con_loader(al_terminar=self.alternar_proxy)
            return
        try:
            puerto = int(self.puerto_proxy.get())
        except ValueError:
            puerto = proxymod.PUERTO_DEFAULT

        if self.almacen_proxy is None:
            self.almacen_proxy = proxymod.Almacen(proxymod.dir_proxy() / "sesion.db")

        self.proxy = proxymod.Proxy(
            self.almacen_proxy, puerto=puerto,
            al_flujo=lambda i: self.cola.put(("proxy_flujo", i)))
        error = self.proxy.iniciar()
        if error:
            self.proxy = None
            messagebox.showerror("Proxy", f"No pude iniciar el proxy:\n{error}")
            return
        self.boton_proxy.configure(text="⏹ Detener proxy")
        self.boton_firefox.configure(state="normal")
        self.estado_proxy.configure(text=f"escuchando en 127.0.0.1:{puerto}",
                                    foreground=COLOR_AGENTE)
        self.refrescar_arbol_proxy()

    def _instalar_mitmproxy(self):
        import subprocess
        # Corre en un hilo: sin try/except, cualquier error se lleva el hilo en
        # silencio y la UI se queda esperando para siempre.
        try:
            r = subprocess.run(
                [interprete.interpreter(), "-m", "pip", "install", "mitmproxy"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            resultado = r.returncode == 0 or (r.stderr or "")[-300:]
        except Exception as e:
            resultado = f"No pude ejecutar pip: {e}"
        self.cola.put(("proxy_instalado", resultado))

    # -- pestaña tareas (los cron) -----------------------------------------

    def _construir_tab_tareas(self):
        panel = ttk.PanedWindow(self.tab_tareas, orient="horizontal")
        panel.pack(fill="both", expand=True, pady=8)

        izq = ttk.Frame(panel, width=320)
        panel.add(izq, weight=0)
        ttk.Label(izq, text="Tareas programadas",
                  font=(FUENTE_UI, 12, "bold")).pack(anchor="w")
        ttk.Label(izq, text="Corren solas; las despierta el sistema.",
                  foreground=COLOR_TENUE, font=(FUENTE_UI, 10)).pack(anchor="w", pady=(0, 6))
        self.lista_tareas = tk.Listbox(izq, font=(FUENTE_UI, 12), activestyle="none",
                                       relief="solid", borderwidth=1, highlightthickness=0,
                                       exportselection=False)
        self.lista_tareas.pack(fill="both", expand=True, pady=(0, 8))
        self.lista_tareas.bind("<<ListboxSelect>>", self._al_elegir_tarea)

        botones = ttk.Frame(izq)
        botones.pack(fill="x")
        ttk.Button(botones, text="↻ Refrescar", command=self.refrescar_tareas).pack(side="left")
        ttk.Button(botones, text="🗑 Borrar", command=self.borrar_tarea_seleccionada).pack(side="left", padx=6)

        # Verificar que la tarea realmente corre.
        acciones = ttk.Frame(izq)
        acciones.pack(fill="x", pady=(6, 0))
        ttk.Button(acciones, text="▶ Correr ahora", command=self.correr_tarea_ahora).pack(side="left")
        ttk.Button(acciones, text="↻ Recargar", command=self.recargar_tarea_sel).pack(side="left", padx=6)

        der = ttk.Frame(panel)
        panel.add(der, weight=1)
        self.detalle_tarea_titulo = ttk.Label(
            der, text="Elegí una tarea para ver de qué trata y sus resultados.",
            font=(FUENTE_UI, 15, "bold"), wraplength=620, justify="left")
        self.detalle_tarea_titulo.pack(anchor="w")
        self.detalle_tarea_meta = ttk.Label(
            der, text="", foreground=COLOR_TENUE, wraplength=620, justify="left")
        self.detalle_tarea_meta.pack(anchor="w", pady=(2, 8))

        ttk.Label(der, text="QUÉ HACE", foreground=COLOR_TENUE,
                  font=(FUENTE_MONO, 9)).pack(anchor="w")
        cont_p, self.detalle_tarea_prompt = self._texto_scroll(
            der, height=4, font=(FUENTE_UI, 12), state="disabled")
        cont_p.pack(fill="x", pady=(2, 8))

        ttk.Label(der, text="CORRIDAS (la más reciente primero)", foreground=COLOR_TENUE,
                  font=(FUENTE_MONO, 9)).pack(anchor="w")
        self.lista_corridas = tk.Listbox(der, height=6, font=(FUENTE_MONO, 11),
                                         activestyle="none", relief="solid", borderwidth=1,
                                         highlightthickness=0, exportselection=False)
        self.lista_corridas.pack(fill="x", pady=(2, 8))
        self.lista_corridas.bind("<<ListboxSelect>>", self._al_elegir_corrida)

        ttk.Label(der, text="RESULTADO DE ESA CORRIDA", foreground=COLOR_TENUE,
                  font=(FUENTE_MONO, 9)).pack(anchor="w")
        cont_r, self.detalle_corrida = self._texto_scroll(
            der, font=(FUENTE_UI, 13), state="disabled")
        cont_r.pack(fill="both", expand=True, pady=(2, 0))

        self.refrescar_tareas()

    def _set_detalle_corrida(self, texto):
        self.detalle_corrida.configure(state="normal")
        self.detalle_corrida.delete("1.0", "end")
        self.detalle_corrida.insert("end", texto or "")
        self.detalle_corrida.configure(state="disabled")

    def _al_elegir_corrida(self, _evento):
        sel = self.lista_corridas.curselection()
        if not sel or not getattr(self, "_corridas", None):
            return
        idx = sel[0]
        if idx >= len(self._corridas):
            return
        c = self._corridas[idx]
        if c.get("ok"):
            self._set_detalle_corrida(c.get("respuesta") or "(sin texto)")
        else:
            self._set_detalle_corrida("⚠️ Falló:\n\n" + (c.get("error") or "error desconocido"))

    def _set_detalle_tarea_prompt(self, texto):
        self.detalle_tarea_prompt.configure(state="normal")
        self.detalle_tarea_prompt.delete("1.0", "end")
        self.detalle_tarea_prompt.insert("end", texto or "")
        self.detalle_tarea_prompt.configure(state="disabled")

    def refrescar_tareas(self):
        from core import programador as prog
        self._tareas = prog.listar_tareas()
        self.lista_tareas.delete(0, "end")
        for t in self._tareas:
            self.lista_tareas.insert("end", f" {self._emoji_tarea(t)} {t['titulo']}   ({prog.describir(t)})")
        if not self._tareas:
            self.lista_tareas.insert("end", "  (no hay tareas programadas)")
        self.detalle_tarea_titulo.configure(text="Elegí una tarea para ver de qué trata y sus resultados.")
        self.detalle_tarea_meta.configure(text="")
        self._set_detalle_tarea_prompt("")
        self.lista_corridas.delete(0, "end")
        self._corridas = []
        self._set_detalle_corrida("")

    def _al_elegir_tarea(self, _evento):
        from core import programador as prog
        sel = self.lista_tareas.curselection()
        if not sel or not getattr(self, "_tareas", None):
            return
        idx = sel[0]
        if idx >= len(self._tareas):
            return
        t = self._tareas[idx]
        self._tarea_sel = t
        est = prog.estado_tarea(t["id"])
        carga = ("programada en el sistema" if est == "programada"
                 else ("AUSENTE — no va a correr, tocá «Recargar»" if est == "ausente" else est))
        tipo = "script (determinística)" if t.get("tipo") == "script" else "agente (usa el modelo)"
        self.detalle_tarea_titulo.configure(text=t["titulo"])
        self.detalle_tarea_meta.configure(text=(
            f"Estado: {self._emoji_tarea(t)} {carga}   ·   tipo: {tipo}\n"
            f"{prog.describir(t)}   ·   conversación: {t['conversacion']}\n"
            f"creada: {t.get('creado', '—')}   ·   "
            f"última corrida: {t.get('ultima_corrida') or 'todavía no corrió'}"))
        self._set_detalle_tarea_prompt(t.get("prompt", ""))

        # Historial de corridas de esta tarea
        self._corridas = prog.corridas(t["id"])
        self.lista_corridas.delete(0, "end")
        for c in self._corridas:
            estado = "✅" if c.get("ok") else "❌"
            self.lista_corridas.insert("end", f" {estado}  {c['ts']}")
        if not self._corridas:
            self.lista_corridas.insert("end", "  (todavía no corrió)")
        self._set_detalle_corrida("")

    def borrar_tarea_seleccionada(self):
        from core import programador as prog
        sel = self.lista_tareas.curselection()
        if not sel or not getattr(self, "_tareas", None):
            messagebox.showinfo("Tareas", "Elegí una tarea de la lista.")
            return
        idx = sel[0]
        if idx >= len(self._tareas):
            return
        t = self._tareas[idx]
        if messagebox.askokcancel(
                "Borrar tarea", f"¿Borrar «{t['titulo']}» ({prog.describir(t)})?\n\n"
                "Deja de correr y se quita del sistema."):
            prog.borrar_tarea(t["id"])
            self.refrescar_tareas()

    def _emoji_tarea(self, t):
        """Semáforo de salud: ⏸ si el sistema no la conoce (no corre), ❌ si la
        última corrida falló, 🕓 si nunca corrió, ✅ si está sana."""
        from core import programador as prog
        try:
            if prog.estado_tarea(t["id"]) == "ausente":
                return "⏸"
            cs = prog.corridas(t["id"])
            if cs and not cs[0].get("ok"):
                return "❌"
            if not cs:
                return "🕓"
            return "✅"
        except Exception:
            return "•"

    def _seleccionar_tarea_por_id(self, tid):
        for i, t in enumerate(getattr(self, "_tareas", [])):
            if t.get("id") == tid:
                self.lista_tareas.selection_clear(0, "end")
                self.lista_tareas.selection_set(i)
                self._al_elegir_tarea(None)
                return

    def correr_tarea_ahora(self):
        """Corre la tarea YA (sin esperar el horario) para verificar que funciona."""
        t = getattr(self, "_tarea_sel", None)
        if not t:
            messagebox.showinfo("Tareas", "Elegí una tarea de la lista.")
            return
        tid = t["id"]
        self._log(f"▶ Corriendo «{t['titulo']}» ahora para verificar…")
        import subprocess
        from core import interprete

        def worker():
            try:
                subprocess.run(
                    [interprete.interpreter(), str(APP_DIR / "correr_tarea.py"), "--tarea", tid],
                    env=os.environ.copy(), timeout=360, capture_output=True, text=True)
            except Exception as e:
                self.cola.put(("log", f"⚠️ error corriendo la tarea: {e}"))
            self.cola.put(("tarea_corrida", tid))

        threading.Thread(target=worker, daemon=True).start()

    def recargar_tarea_sel(self):
        from core import programador as prog
        t = getattr(self, "_tarea_sel", None)
        if not t:
            messagebox.showinfo("Tareas", "Elegí una tarea de la lista.")
            return
        r = prog.recargar(t["id"])
        if r.get("error"):
            messagebox.showerror("Recargar", f"No pude recargarla:\n{r['error']}")
        else:
            messagebox.showinfo("Recargar", "Recargada en el sistema. Ya debería correr según su agenda.")
        self.refrescar_tareas()
        self._seleccionar_tarea_por_id(t["id"])

    def _al_cambiar_pestana(self, _evento=None):
        try:
            actual = self.pestanas.tab(self.pestanas.select(), "text")
        except Exception:
            return
        if "Tareas" in actual:
            self.refrescar_tareas()

    def abrir_programador(self):
        """Diálogo para que el agente corra solo a una hora fija (vía launchd).
        Programa el prompt sobre la conversación actual."""
        from core import programador as prog

        if self.conversacion is None:
            messagebox.showinfo(
                "Programar", "Elegí o creá una conversación primero: la tarea corre "
                "sobre esa conversación.")
            return

        conv_nombre = self.conversacion.conversacion_dir.name
        conv_titulo = self.conversacion.meta.get("titulo", conv_nombre)

        top = tk.Toplevel(self)
        top.title("Programar tarea")
        top.transient(self)
        top.configure(padx=20, pady=18)
        top.resizable(False, False)

        ttk.Label(top, text=f"Sobre: {conv_titulo}",
                  font=(FUENTE_UI, 12, "bold")).pack(anchor="w")
        ttk.Label(top, text="El sistema despierta a AgentFactory a la hora fijada, "
                  "corre esto y te deja el resultado acá + una notificación.",
                  foreground=COLOR_TENUE, wraplength=440,
                  justify="left").pack(anchor="w", pady=(2, 10))

        ttk.Label(top, text="¿Qué querés que haga?").pack(anchor="w")
        caja = tk.Text(top, height=4, width=54, wrap="word", font=(FUENTE_UI, 12),
                       relief="solid", borderwidth=1)
        caja.pack(fill="x", pady=(3, 10))
        caja.insert("1.0", "Revisá lo que capturé de LinkedIn y armame un JSON "
                    "con los trabajos y perfiles nuevos.")

        fila = ttk.Frame(top)
        fila.pack(anchor="w")
        ttk.Label(fila, text="Todos los días a las").pack(side="left")
        hora = tk.StringVar(value="10")
        minuto = tk.StringVar(value="00")
        ttk.Spinbox(fila, from_=0, to=23, width=4, textvariable=hora, format="%02.0f").pack(side="left", padx=(8, 2))
        ttk.Label(fila, text=":").pack(side="left")
        ttk.Spinbox(fila, from_=0, to=59, width=4, textvariable=minuto, format="%02.0f").pack(side="left", padx=(2, 0))

        lista = tk.Listbox(top, height=5, font=(FUENTE_MONO, 11), relief="solid",
                           borderwidth=1, highlightthickness=0)

        def refrescar():
            lista.delete(0, "end")
            self._tareas_prog = prog.listar_tareas()
            for t in self._tareas_prog:
                lista.insert("end", f" {prog.describir(t)}  ·  {t['titulo']}  ·  {t['conversacion']}")
            if not self._tareas_prog:
                lista.insert("end", "  (no hay tareas programadas todavía)")

        def crear():
            texto = caja.get("1.0", "end").strip()
            if not texto:
                messagebox.showwarning("Programar", "Escribí qué querés que haga.", parent=top)
                return
            try:
                h, m = int(hora.get()), int(minuto.get())
            except ValueError:
                messagebox.showwarning("Programar", "Hora inválida.", parent=top)
                return
            r = prog.crear_tarea(titulo=conv_titulo, conversacion=conv_nombre,
                                 prompt=texto, hora=h, minuto=m)
            if isinstance(r, dict) and "error" in r:
                messagebox.showerror("Programar", f"No pude programarla:\n{r['error']}", parent=top)
                return
            refrescar()

        def borrar():
            sel = lista.curselection()
            if not sel or not getattr(self, "_tareas_prog", None):
                return
            idx = sel[0]
            if idx >= len(self._tareas_prog):
                return
            t = self._tareas_prog[idx]
            if messagebox.askokcancel("Borrar tarea",
                                      f"¿Borrar «{t['titulo']}» ({prog.describir(t)})?", parent=top):
                prog.borrar_tarea(t["id"])
                refrescar()

        botones = ttk.Frame(top)
        botones.pack(fill="x", pady=(12, 8))
        ttk.Button(botones, text="✓ Programar", command=crear).pack(side="left")
        ttk.Label(top, text="Programadas:", foreground=COLOR_TENUE).pack(anchor="w")
        lista.pack(fill="x", pady=(3, 4))
        ttk.Button(top, text="🗑 Borrar seleccionada", command=borrar).pack(anchor="w")

        refrescar()
        caja.focus_set()

    def _mitmproxy_disponible(self) -> bool:
        try:
            import mitmproxy  # noqa: F401
            return True
        except ImportError:
            return False

    def _instalar_mitmproxy_con_loader(self, al_terminar=None):
        """Instala mitmproxy mostrando un loader (sin preguntar). Al terminar
        bien, llama a `al_terminar` para continuar el flujo que lo pidió."""
        if getattr(self, "_instalando_mitm", False):
            return
        self._instalando_mitm = True

        top = tk.Toplevel(self)
        top.title("AgentFactory")
        top.transient(self)
        top.resizable(False, False)
        top.configure(padx=26, pady=22)
        ttk.Label(top, text="Preparando el navegador de captura…",
                  font=(FUENTE_UI, 13, "bold")).pack(anchor="w")
        ttk.Label(top, text="Instalando mitmproxy (una sola vez).",
                  foreground=COLOR_TENUE).pack(anchor="w", pady=(2, 12))
        barra = ttk.Progressbar(top, mode="indeterminate", length=300)
        barra.pack(fill="x")
        barra.start(12)
        top.update_idletasks()
        # centrar sobre la ventana principal
        try:
            x = self.winfo_rootx() + (self.winfo_width() - top.winfo_width()) // 2
            y = self.winfo_rooty() + (self.winfo_height() - top.winfo_height()) // 3
            top.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass
        top.grab_set()

        estado = {}

        def worker():
            import subprocess
            r = subprocess.run(
                [interprete.interpreter(), "-m", "pip", "install", "mitmproxy"],
                capture_output=True, text=True)
            estado["ok"] = (r.returncode == 0)
            estado["err"] = (r.stderr or r.stdout or "").strip()[-500:]

        hilo = threading.Thread(target=worker, daemon=True)
        hilo.start()

        def revisar():
            if hilo.is_alive():
                self.after(200, revisar)
                return
            barra.stop()
            top.destroy()
            self._instalando_mitm = False
            if estado.get("ok") and self._mitmproxy_disponible():
                if al_terminar:
                    al_terminar()
            else:
                messagebox.showerror(
                    "Preparar captura",
                    "No pude instalar mitmproxy:\n" + estado.get("err", ""))

        self.after(200, revisar)

    def abrir_firefox_proxy(self):
        # mitmproxy tiene que estar antes de arrancar el proxy: si falta, loader
        # (sin preguntar) y, cuando termina, reintenta esta misma acción.
        if not self._mitmproxy_disponible():
            self._instalar_mitmproxy_con_loader(al_terminar=self.abrir_firefox_proxy)
            return
        # Firefox sin proxy corriendo no sirve para capturar: si no está
        # escuchando, lo arrancamos primero (un solo clic hace todo).
        if not (self.proxy and self.proxy.corriendo):
            self.alternar_proxy()   # inicia (o pide instalar mitmproxy)
            if not (self.proxy and self.proxy.corriendo):
                # Lo declinaste, falló el arranque, o mitmproxy se está
                # instalando en segundo plano: alternar_proxy ya avisó. Cuando
                # termine, volvé a tocar «Abrir Firefox».
                return
        try:
            puerto = int(self.puerto_proxy.get())
        except (ValueError, TypeError):
            puerto = proxymod.PUERTO_DEFAULT
        try:
            r = proxymod.lanzar_firefox(puerto)
        except Exception as e:
            messagebox.showerror("Firefox", f"No pude abrir Firefox: {e}")
            return
        if "error" in r:
            messagebox.showerror("Firefox", r["error"])
            return
        # Éxito: sin popup. Firefox abre y listo.

    def limpiar_proxy(self):
        if self.almacen_proxy is None:
            return
        if not messagebox.askokcancel("Limpiar", "¿Borrar todo el tráfico capturado?"):
            return
        self.almacen_proxy.borrar_todo()
        self.refrescar_arbol_proxy()
        for caja in (self.proxy_req_h, self.proxy_req_b, self.proxy_resp_h, self.proxy_resp_b):
            self._escribir_recuadro(caja, "", "hdr")


    @staticmethod
    def _emoji_flujo(nodo, es_host):
        """Emoji según qué es el nodo. Host y nodos intermedios (con hijos y sin
        respuesta propia) son 'carpetas'; las hojas van por content-type."""
        f = nodo.get("_flujo")
        if es_host:
            return "🌐"
        if f is None or nodo.get("_hijos"):
            return "📁"
        tipo = (f.get("resp_tipo") or "").lower()
        estado = f.get("estado") or 0
        if estado >= 400:
            return "🚫"                     # error / no encontrado
        if estado >= 300:
            return "↪️"                     # redirección
        if "html" in tipo:
            return "📄"
        if "json" in tipo:
            return "🔧"
        if "javascript" in tipo or "ecmascript" in tipo:
            return "📜"
        if "css" in tipo:
            return "🎨"
        if "image" in tipo:
            return "🖼"
        if "font" in tipo:
            return "🔤"
        if any(x in tipo for x in ("pdf", "zip", "octet-stream", "download")):
            return "📦"
        if any(x in tipo for x in ("audio", "video")):
            return "🎬"
        if "xml" in tipo:
            return "📑"
        if tipo.startswith("text/"):
            return "📃"
        return "•"

    # --- filtro y expansión del árbol -------------------------------------

    EXTENSIONES_ESTATICAS = (".css", ".js", ".mjs", ".png", ".jpg", ".jpeg",
                             ".gif", ".svg", ".webp", ".ico", ".woff", ".woff2",
                             ".ttf", ".otf", ".map")
    TIPOS_ESTATICOS = ("css", "javascript", "image/", "font", "ecmascript")

    def _pasa_filtro(self, flujo):
        """¿Un flujo (hoja) sobrevive al filtro y a los chips?"""
        if flujo is None:
            return True
        ruta = (flujo.get("ruta") or "").lower()
        tipo = (flujo.get("resp_tipo") or "").lower()

        if self.ocultar_estaticos.get():
            if ruta.endswith(self.EXTENSIONES_ESTATICAS) or any(t in tipo for t in self.TIPOS_ESTATICOS):
                return False

        patron = self.filtro_proxy.get().strip().lower()
        if not patron:
            return True
        # 'contiene' por defecto; soporta comodines tipo *.jpg o /api/*
        if "*" in patron or "?" in patron:
            import fnmatch
            objetivo = ruta if patron.startswith(("*", "/")) else f"{flujo.get('host','')}{ruta}"
            return fnmatch.fnmatch(objetivo, patron) or fnmatch.fnmatch(ruta, patron)
        # negación con '!': !css oculta lo que contiene css
        if patron.startswith("!"):
            return patron[1:] not in ruta and patron[1:] not in tipo
        return patron in ruta or patron in tipo or patron in str(flujo.get("estado", ""))

    def _expandir_arbol(self, abrir):
        def rec(n=""):
            for h in self.arbol_proxy.get_children(n):
                self.arbol_proxy.item(h, open=abrir)
                rec(h)
        rec()

    def _autoancho_arbol(self):
        """Ajusta el ancho de la columna del árbol al texto más largo visible."""
        import tkinter.font as tkfont
        fuente = tkfont.Font(font=(FUENTE_UI, 12))
        ancho = 300
        def medir(nodo=""):
            nonlocal ancho
            for h in self.arbol_proxy.get_children(nodo):
                texto = self.arbol_proxy.item(h, "text")
                prof = 0
                p = h
                while self.arbol_proxy.parent(p):
                    prof += 1; p = self.arbol_proxy.parent(p)
                ancho = max(ancho, fuente.measure(texto) + 40 + prof * 20)
                medir(h)
        medir()
        self.arbol_proxy.column("#0", width=min(ancho, 1200))

    def _nodo_visible(self, nodo):
        """True si el nodo mismo pasa el filtro, o si alguno de sus hijos lo
        hace (para no cortar la rama que lleva a un resultado)."""
        f = nodo.get("_flujo")
        if f is not None and self._pasa_filtro(f):
            return True
        return any(self._nodo_visible(h) for h in nodo.get("_hijos", {}).values())

    def reactivar_ignoradas(self):
        """Vacía la lista negra: las URLs borradas se vuelven a capturar."""
        if self.almacen_proxy is None:
            return
        self.almacen_proxy.dejar_de_ignorar(None)
        self._log("🔊 Lista negra vaciada: se vuelven a capturar todas las URLs")

    def refrescar_arbol_proxy(self):
        if self.almacen_proxy is None:
            return
        self.arbol_proxy.delete(*self.arbol_proxy.get_children())
        self._flujo_de_nodo = {}
        self._url_de_nodo = {}
        arbol = proxymod.arbol_de(self.almacen_proxy.listar_arbol())
        filtrando = bool(self.filtro_proxy.get().strip()) or self.ocultar_estaticos.get()

        def insertar(padre, nombre, nodo, es_host=False):
            if not self._nodo_visible(nodo):
                return
            f = nodo.get("_flujo")
            info = f"{f['metodo']} · {f['estado']}" if f else ""
            # 'index.php (id, p)': nombres de parámetros GET/POST en la hoja.
            params = nodo.get("_params") or []
            etiqueta = f"{nombre} ({', '.join(params)})" if params else nombre
            # Un path que solo existe por un reenvío se marca con 🔁.
            reenv = "🔁 " if f and f.get("origen") == "reenvio" else ""
            emoji = self._emoji_flujo(nodo, es_host)
            iid = self.arbol_proxy.insert(padre, "end", text=f"{emoji}  {reenv}{etiqueta}",
                                          values=(info,), open=bool(filtrando))
            if f:
                self._flujo_de_nodo[iid] = f["id"]
                self._url_de_nodo[iid] = (f["host"], f["ruta"])
            for sub in sorted(k for k in nodo["_hijos"]):
                insertar(iid, sub, nodo["_hijos"][sub])

        for host in sorted(arbol):
            insertar("", host, arbol[host], es_host=True)

        n = len(self._flujo_de_nodo)
        if filtrando:
            self.estado_proxy.configure(text=f"{n} resultado(s)")

    def _borrar_seleccion_proxy(self, _evento=None):
        self._borrar_nodos_proxy(self.arbol_proxy.selection())
        return "break"

    def _urls_bajo_nodo(self, nodo):
        """(host, ruta) de este nodo y de todos sus descendientes. Borrar una
        'carpeta' se lleva todas las URLs que cuelgan de ella."""
        pares = set()
        if nodo in self._url_de_nodo:
            pares.add(self._url_de_nodo[nodo])
        for hijo in self.arbol_proxy.get_children(nodo):
            pares |= self._urls_bajo_nodo(hijo)
        return pares

    def _borrar_nodos_proxy(self, nodos):
        """Borra TODOS los flujos de las URLs bajo los nodos seleccionados —
        todas las capturas de cada URL, no solo la representativa."""
        pares = set()
        for n in nodos:
            pares |= self._urls_bajo_nodo(n)
        if not pares:
            return
        detalle = (f"{len(nodos)} elementos seleccionados" if len(nodos) > 1
                   else self.arbol_proxy.item(nodos[0], "text").strip())
        if not messagebox.askokcancel(
                "Borrar del árbol",
                f"«{detalle}»\n\nBorra {len(pares)} URL(s) y TODAS sus capturas "
                "de la base, y deja de capturarlas (si no, el proxy las vuelve a "
                "traer al toque)."):
            return
        n = self.almacen_proxy.borrar_por_url(pares)
        self.almacen_proxy.ignorar(pares)     # que no se re-capturen en vivo
        self._log(f"🗑 {n} flujo(s) borrado(s) · {len(pares)} URL(s) en lista negra")
        for caja in (self.proxy_req_h, self.proxy_req_b, self.proxy_resp_h, self.proxy_resp_b):
            self._escribir_recuadro(caja, "", "hdr")
        self.refrescar_arbol_proxy()

    def _al_elegir_flujo(self, _evento=None):
        sel = self.arbol_proxy.selection()
        if len(sel) != 1:          # con multi-selección no tiene sentido un detalle
            return
        id_flujo = self._flujo_de_nodo.get(sel[0])
        if id_flujo is not None:
            self._mostrar_flujo(id_flujo)

    def _mostrar_flujo(self, id_flujo):
        f = self.almacen_proxy.obtener(id_flujo)
        if f is None:
            return
        q = f"?{f['query']}" if f["query"] else ""

        # -- Petición --
        self._escribir_recuadro(self.proxy_req_h, "", "hdr")
        self.proxy_req_h.configure(state="normal")
        self.proxy_req_h.insert("end", f"{f['metodo']} {f['ruta']}{q}\n", "linea1")
        self.proxy_req_h.insert("end", proxymod.texto_headers(f["req_headers"]), "hdr")
        self.proxy_req_h.configure(state="disabled")

        enc_req = proxymod._content_encoding(f["req_headers"])
        cr = proxymod.cuerpo_legible(proxymod.descomprimir(f["req_body"], enc_req),
                                     proxymod._content_type_de(f["req_headers"]))
        self._cuerpo_a_caja(self.proxy_req_b, cr)

        # -- Respuesta --
        self._escribir_recuadro(self.proxy_resp_h, "", "hdr")
        self.proxy_resp_h.configure(state="normal")
        self.proxy_resp_h.insert("end",
            f"{f['estado']}  ·  {f['resp_tipo']}  ·  "
            f"{formato.size(len(f['resp_body'] or b''))}\n", "linea1")
        self.proxy_resp_h.insert("end", proxymod.texto_headers(f["resp_headers"]), "hdr")
        self.proxy_resp_h.configure(state="disabled")

        enc = proxymod._content_encoding(f["resp_headers"])
        cbody = proxymod.cuerpo_legible(proxymod.descomprimir(f["resp_body"], enc), f["resp_tipo"])
        self._cuerpo_a_caja(self.proxy_resp_b, cbody)

    def _cuerpo_a_caja(self, caja, cuerpo):
        caja.configure(state="normal"); caja.delete("1.0", "end")
        if cuerpo.get("binario"):
            caja.insert("end", "(binario · hexadecimal)\n\n", "aviso")
        caja.insert("end", cuerpo.get("texto", ""))
        caja.configure(state="disabled")

    def _menu_flujo(self, evento):
        nodo = self.arbol_proxy.identify_row(evento.y)
        if not nodo or nodo not in self._flujo_de_nodo:
            return
        if nodo not in self.arbol_proxy.selection():
            self.arbol_proxy.selection_set(nodo)
        id_flujo = self._flujo_de_nodo[nodo]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="↻ Reenviar / editar…",
                         command=lambda: self._abrir_repeater(id_flujo))
        menu.add_separator()
        menu.add_command(label="🔖 Marcar para la conversación",
                         command=lambda: self._marcar_flujo(id_flujo))
        menu.add_separator()
        sel = self.arbol_proxy.selection()
        if len(sel) > 1:
            menu.add_command(label=f"🗑 Borrar {len(sel)} seleccionados",
                             command=lambda: self._borrar_nodos_proxy(sel))
        else:
            menu.add_command(label="🗑 Borrar del árbol",
                             command=lambda: self._borrar_nodos_proxy((nodo,)))
        menu.tk_popup(evento.x_root, evento.y_root)

    def _marcar_flujo(self, id_flujo):
        """Marca un flujo con una etiqueta y deja lista una referencia para
        seguirlo en la conversación (@flujo <id>)."""
        from core import marcas
        etiqueta = simpledialog.askstring(
            "Marcar flujo",
            "Etiqueta (ej: stored-xss, path-disclosure, idor):", parent=self)
        if not etiqueta:
            return
        nota = simpledialog.askstring("Marcar flujo",
                                      "Nota (opcional):", parent=self) or None
        db = proxymod.dir_proxy() / "sesion.db"
        marcas.mark_flow(db, id_flujo, etiqueta.strip(), nota)
        self._log(f"🔖 Flujo {id_flujo} marcado como «{etiqueta.strip()}»")
        if messagebox.askyesno(
                "Marcar flujo",
                f"Flujo {id_flujo} marcado como «{etiqueta.strip()}».\n\n"
                "¿Llevarlo a una conversación para analizarlo con el agente?"):
            self._llevar_flujo_a_conversacion(id_flujo, etiqueta.strip())

    def _llevar_flujo_a_conversacion(self, id_flujo, etiqueta):
        """Va a la pestaña de conversaciones y deja escrito el pedido con la
        referencia @flujo, que el agente resuelve con la tool de proxy."""
        self.pestanas.select(self.tab_chat)
        if self.conversacion is None:
            ruta = self.gestor_conversaciones.crear_conversacion(f"proxy · {etiqueta}")
            self.refrescar_conversaciones()
            self._cargar_conversacion(ruta)
        self.entrada.delete("1.0", "end")
        self.entrada.insert("1.0",
            f"Analizá el flujo @flujo {id_flujo} del proxy (lo marqué como "
            f"«{etiqueta}»). Traé su contexto y decime qué ves.")
        self.entrada.focus_set()

    def _ids_bajo_nodo(self, nodo):
        """Todos los id de flujo de un nodo y sus descendientes (borrar una
        'carpeta' borra todo lo que cuelga de ella)."""
        ids = []
        f = self._flujo_de_nodo.get(nodo)
        if f is not None:
            ids.append(f)
        for hijo in self.arbol_proxy.get_children(nodo):
            ids += self._ids_bajo_nodo(hijo)
        return ids

    def _abrir_repeater(self, id_flujo):
        flujo = self.almacen_proxy.obtener(id_flujo)
        if flujo is None:
            return
        DialogoReenvio(self, self.almacen_proxy, flujo)
        # Al cerrar la ventana, un path nuevo descubierto por un reenvío ya
        # está en la base: refrescamos el árbol para que aparezca.
        self.refrescar_arbol_proxy()

    # -- utilidades de UI --------------------------------------------------

    def _al_fondo(self) -> bool:
        """¿La transcripción está scrolleada hasta el fondo? Si el usuario subió
        a leer, devolvemos False y no forzamos el scroll."""
        try:
            return self.transcripcion.yview()[1] >= 0.999
        except Exception:
            return True

    def _seguir_fondo(self, estaba_al_fondo=True):
        """Baja al final SOLO si ya estabas al final (o se fuerza)."""
        if estaba_al_fondo:
            self.transcripcion.see("end")

    @staticmethod
    def _hora(ts: str = None) -> str:
        """HH:MM:SS. Si viene un ts guardado ('%Y-%m-%d %H:%M:%S'), usa esa hora
        (para el historial); si no, la hora actual (para lo que llega en vivo)."""
        if ts and len(ts) >= 19:
            return ts[11:19]
        return time.strftime("%H:%M:%S")

    def _agregar_mensaje(self, quien: str, texto: str, tag_rol: str, ts: str = None):
        # Tu mensaje al enviar siempre baja al fondo; el resto (respuesta del
        # agente, render final) solo si ya estabas al fondo — así no te saca de
        # donde estabas leyendo.
        es_user = tag_rol == "rol_usuario"
        al_fondo = es_user or self._al_fondo()
        self.transcripcion.configure(state="normal")
        inicio = self.transcripcion.index("end-1c")

        if es_user:
            # Tu mensaje: globo verde a la derecha, texto tal cual.
            self.transcripcion.insert("end", f"{texto}\n", "globo_user")
            self.transcripcion.tag_add("globo_user", inicio, self.transcripcion.index("end-1c"))
            self.transcripcion.insert("end", f"{self._hora(ts)}\n", "hora_user")
        else:
            # Agente: Markdown adentro de un globo blanco a la izquierda.
            render_markdown.render(
                self.transcripcion, texto,
                al_ejecutar=self._ejecutar_bloque,
                base_imagenes=self.conversacion.workspace_dir if self.conversacion else None,
                al_abrir_imagen=lambda ruta: plataforma.abrir(ruta),
                al_abrir=self._abrir_destino)
            if texto.strip():
                self._insertar_boton_copiar(texto)
            # El globo se aplica sobre TODO el tramo renderizado y se manda al
            # fondo de la pila de tags, para que el color/typografía del Markdown
            # (y el fondo propio de los bloques de código) sigan ganando.
            self.transcripcion.tag_add("globo_agent", inicio, self.transcripcion.index("end-1c"))
            self.transcripcion.tag_lower("globo_agent")
            self.transcripcion.insert("end", f"\n{self._hora(ts)}\n", "hora_agent")

        self.transcripcion.insert("end", "\n")
        if es_user:
            self.transcripcion._hubo_intercambio = True
        self.transcripcion.configure(state="disabled")
        self._seguir_fondo(al_fondo)

    # -- Copiar el resultado (ícono discreto al final de cada respuesta) ----

    def _insertar_boton_copiar(self, texto: str):
        """Deja un 📋 clickeable al final de la respuesta del agente. El texto a
        copiar queda guardado bajo una clave única que viaja en el tag."""
        self._n_copia += 1
        clave = f"copia-{self._n_copia}"
        self._copiables[clave] = texto
        self.transcripcion.insert("end", "  ")
        self.transcripcion.insert("end", "📋", ("copiar", clave))

    def _al_click_copiar(self, evento):
        idx = self.transcripcion.index(f"@{evento.x},{evento.y}")
        clave = next((t for t in self.transcripcion.tag_names(idx)
                      if t.startswith("copia-")), None)
        texto = self._copiables.get(clave) if clave else None
        if not texto:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(texto)
        self._flash_copiar(clave, "✅")
        return "break"

    def _flash_copiar(self, clave: str, glifo: str):
        """Cambia el ícono a ✅ un instante y lo vuelve a 📋, como confirmación."""
        rango = self.transcripcion.tag_ranges(clave)
        if not rango:
            return
        self.transcripcion.configure(state="normal")
        self.transcripcion.delete(rango[0], rango[1])
        self.transcripcion.insert(rango[0], glifo, ("copiar", clave))
        self.transcripcion.configure(state="disabled")
        if glifo != "📋":
            self.after(1200, lambda: self._flash_copiar(clave, "📋"))

    def _abrir_destino(self, destino: str):
        """Clic en una ruta o URL de la conversación: la abre como lo haría
        `open` en la terminal. Si no existe, lo dice en vez de no hacer nada."""
        error = plataforma.abrir(destino)
        if error:
            self._log(f"⚠️ {error}")

    def _ejecutar_bloque(self, codigo: str, motor: str):
        """▶ Ejecutar de un bloque de código. Pasa por el mismo guard que el
        agente: si borra algo, te pregunta igual."""
        if self.ocupado_chat:
            messagebox.showinfo("Ejecutar", "Esperá a que termine el turno en curso.")
            return
        if not messagebox.askokcancel(
                "Ejecutar bloque",
                f"Se va a ejecutar este bloque de {motor} en tu máquina:\n\n"
                f"{codigo[:400]}{'…' if len(codigo) > 400 else ''}"):
            return

        permisos = self.conversacion.permisos if self.conversacion else None
        base = self.conversacion.workspace_dir if self.conversacion else None
        self._log(f"▶ ejecutando bloque de {motor}…")

        def trabajo():
            fn = ejecucion.ejecutar_python if motor == "python" else ejecucion.ejecutar_shell
            self.cola.put(("bloque_ok", fn(codigo, base=base, permisos=permisos)))

        threading.Thread(target=trabajo, daemon=True).start()

    def _aviso_transcripcion(self, texto: str):
        self.transcripcion.configure(state="normal")
        self.transcripcion.insert("end", f"{texto}\n", "aviso")
        self.transcripcion.configure(state="disabled")
        self.transcripcion.see("end")

    def _log(self, linea: str):
        """Toda la salida de consola del agente va al log de proyectos; si hay
        un turno de chat en curso, las tool calls se muestran también ahí."""
        self.log_proyecto.configure(state="normal")
        if linea.strip():
            self.log_proyecto.insert("end", f"[{time.strftime('%H:%M:%S')}] {linea}\n", "normal")
        else:
            self.log_proyecto.insert("end", linea + "\n", "normal")
        self.log_proyecto.configure(state="disabled")
        self.log_proyecto.see("end")

        if self.ocupado_chat and linea.strip():
            self.transcripcion.configure(state="normal")
            self.transcripcion.insert("end", linea.strip() + "\n", "tool")
            self.transcripcion.configure(state="disabled")
            self.transcripcion.see("end")

    def _al_refrescar(self, _evento=None):
        self._titulo_provisorio = None
        aviso = configuracion.asegurar_precios()
        if aviso:
            print(f'💲 {aviso}')

        self.refrescar_todo()
        self.mostrar_bienvenida()
        return "break"

    def _al_versiones(self, _evento=None):
        if self.ocupado_chat or self.ocupado_proyecto:
            messagebox.showwarning(
                "Versiones",
                "Hay trabajo en curso. Esperá a que termine antes de restaurar código.")
            return "break"
        DialogoVersiones(self, self.gestor_versiones)
        return "break"

    def refrescar_todo(self):
        """Relee todo desde disco (⌘R). Mantiene la selección de la conversación
        activa y, si no hay un turno en curso, recarga su transcripción — así se
        ve lo que haya escrito otra instancia del agente en la misma carpeta."""
        activa = self.conversacion.conversacion_dir if self.conversacion else None

        self.refrescar_conversaciones()
        self.refrescar_proyectos()
        self.refrescar_biblioteca()

        if activa is not None:
            for i, c in enumerate(self.conversaciones):
                if c["path"] == activa:
                    self.lista_conversaciones.selection_clear(0, "end")
                    self.lista_conversaciones.selection_set(i)
                    if not self.ocupado_chat:
                        self._cargar_conversacion(activa)
                    break

        self.estado_chat.configure(text="↻ Actualizado")
        self.after(1500, lambda: self.estado_chat.configure(text="")
                   if not self.ocupado_chat else None)

    def abrir_workspace(self):
        destino = self.conversacion.workspace_dir if self.conversacion else AGENT_CODE_DIR
        plataforma.abrir_carpeta(destino)

    def _procesar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()

                if tipo == "log":
                    self._log(dato)

                elif tipo == "tarea_corrida":
                    self._log("✅ Corrida manual terminada — mirá el estado y el historial de la tarea.")
                    self.refrescar_tareas()
                    self._seleccionar_tarea_por_id(dato)

                elif tipo == "chat_delta":
                    ruta, fragmento = dato
                    # Solo se pinta si estás mirando ESA conversación.
                    if self.conversacion is not None and \
                            self.conversacion.conversacion_dir == ruta:
                        al_fondo = self._al_fondo()
                        self.transcripcion.configure(state="normal")
                        self.transcripcion.insert("end", fragmento, "cuerpo")
                        self.transcripcion.configure(state="disabled")
                        # Solo sigo al final si YA estabas al final; si subiste a
                        # leer, te dejo donde estás.
                        self._seguir_fondo(al_fondo)

                elif tipo in ("chat_ok", "chat_error"):
                    ruta, contenido = dato
                    self.ocupadas.discard(ruta)
                    self.inicio_turno.pop(ruta, None)
                    if tipo == "chat_error":
                        contenido = f"⚠️ Error inesperado:\n{contenido}"

                    if self.conversacion is not None and \
                            self.conversacion.conversacion_dir == ruta:
                        self._cerrar_stream(ruta, contenido, tipo == "chat_error")
                    else:
                        # Terminó una conversación que no estás mirando: no le
                        # pisamos la pantalla, solo avisamos.
                        self._log(f"✅ «{ruta.name}» terminó su turno "
                                  f"(la estás viendo desde otra conversación)")
                    self._sincronizar_controles_chat()
                    self.refrescar_conversaciones()
                    self.refrescar_biblioteca()
                    if tipo == "chat_ok":
                        self._evaluar_continuacion(ruta, contenido)

                elif tipo == "proyecto_ok":
                    exito = "✅ ¡OBJETIVO ALCANZADO!" if dato["exito"] else "⏸️ Terminado sin alcanzar el umbral"
                    self._log(f"\n{'=' * 50}\n📊 {exito}\n"
                              f"   Iteraciones: {dato['iteraciones']}\n"
                              f"   Mejor puntaje: {formato.percent(dato['mejor_puntaje'], 2)}\n"
                              f"   Proyecto: {dato['proyecto_dir']}")
                    self._fin_proyecto()

                elif tipo == "precios":
                    self._mostrar_precios(dato)

                elif tipo == "proxy_instalado":
                    self.boton_proxy.configure(state="normal")
                    if dato is True:
                        self.estado_proxy.configure(text="mitmproxy listo", foreground=COLOR_AGENTE)
                        self.alternar_proxy()
                    else:
                        self.estado_proxy.configure(text="detenido", foreground=COLOR_TENUE)
                        messagebox.showerror("Instalar mitmproxy",
                                             f"No pude instalarlo:\n{dato}")

                elif tipo == "proxy_flujo":
                    self._flujos_pendientes += 1
                    # Se refresca de a lotes para no reconstruir el árbol en
                    # cada request de una carga de página.
                    if self._flujos_pendientes == 1:
                        self.after(400, self._refrescar_proxy_lote)

                elif tipo == "config_prueba":
                    proveedor, r = dato
                    if r.get("ok"):
                        self._escribir_config(
                            f"✅ {proveedor} responde · modelo {r['modelo']} · "
                            f"{formato.compact(r['tokens'])} tokens · dijo {r['respuesta']!r}\n", "ok")
                    else:
                        self._escribir_config(f"❌ {proveedor}: {r['detalle']}\n", "mal")
                        if r.get("pista"):
                            self._escribir_config(f"   → {r['pista']}\n", "mal")

                elif tipo == "bloque_ok":
                    if "error" in dato:
                        cuerpo = f"```\n{dato['error']}\n```"
                    else:
                        salida = (dato.get("stdout") or "").rstrip() or "(sin salida)"
                        if dato.get("stderr"):
                            salida += "\n--- stderr ---\n" + dato["stderr"].rstrip()
                        cuerpo = (f"**Código de salida:** {dato.get('codigo_retorno')}\n\n"
                                  f"```\n{salida}\n```")
                    self._agregar_mensaje("▶ Resultado", cuerpo, "rol_agente")

                elif tipo == "confirmar":
                    resumen, detalle, clave, evento, caja = dato
                    try:
                        caja.append(DialogoConfirmacion(self, resumen, detalle, clave).decision)
                    except Exception:
                        caja.append("no")
                    finally:
                        evento.set()

                elif tipo == "proc_ok":
                    self._mostrar_resultado_procesador(dato)
                    self._fin_procesador()

                elif tipo == "proc_error":
                    self._escribir_proc(f"❌ {dato}\n", "err")
                    self._fin_procesador()

                elif tipo == "proyecto_error":
                    self._log(f"\n❌ Error inesperado:\n{dato}")
                    self._fin_proyecto()

        except queue.Empty:
            pass

        self.after(120, self._procesar_cola)

    def _refrescar_proxy_lote(self):
        self._flujos_pendientes = 0
        if self.almacen_proxy is not None:
            self.refrescar_arbol_proxy()

    def _fin_proyecto(self):
        self.ocupado_proyecto = False
        self.boton_detener.configure(state="disabled")
        self.boton_ejecutar.configure(state="normal")
        self.refrescar_proyectos()
        self.refrescar_biblioteca()
        # Vuelve a habilitar «Publicar» si el proyecto elegido ya tiene código.
        self._al_elegir_proyecto(None)

    def _al_cerrar(self):
        if self.ocupado_chat or self.ocupado_proyecto:
            if not messagebox.askokcancel(
                "AgentFactory",
                "Hay trabajo en curso. Lo hecho hasta ahora ya está guardado en disco, "
                "pero la iteración actual se pierde.\n\n¿Cerrar igual?"
            ):
                return
        self.destroy()


def main():
    # Antes de crear la ventana: sin esto Windows escala toda la interfaz
    # como un bitmap en pantallas al 125/150% y se ve borrosa.
    escala = plataforma.preparar_dpi()
    app = AgenteUI()
    if escala:
        # Hacerse consciente del DPI sin compensar deja todo diminuto.
        try:
            app.tk.call("tk", "scaling", escala)
        except Exception:
            pass
    app.mainloop()


if __name__ == "__main__":
    main()
