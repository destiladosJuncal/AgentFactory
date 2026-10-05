"""
Bridge between the JS front-end and Python, for the pywebview chat view.

The front-end calls these methods through ``window.pywebview.api.<name>`` and the
bridge pushes updates back into the page with ``window.evaluate_js`` (that's how
streaming works). It reuses the exact same backend as the Tkinter UI
(``GestorConversaciones`` + ``ConversacionChat``) — only the presentation changes.

New code, in English, except for the strings that are shown to the user.
"""

import json
import threading
import traceback
from typing import Any, Dict, List

import markdown as _md

from core.conversaciones import GestorConversaciones
from core.chat import ConversacionChat

# Markdown → HTML. 'tables' is the whole point of this migration; 'fenced_code'
# for code blocks; 'sane_lists'/'nl2br' so lists and line breaks behave.
_EXTS = ["tables", "fenced_code", "sane_lists", "nl2br"]


def md_a_html(texto: str) -> str:
    return _md.markdown(texto or "", extensions=_EXTS, output_format="html5")


class Puente:
    def __init__(self):
        self.gestor = GestorConversaciones()
        self._chats: Dict[str, ConversacionChat] = {}
        self.window = None            # se setea después de create_window

    # -- JS helper ----------------------------------------------------------

    def _js(self, fn: str, *args):
        """Llama una función JS del front con argumentos JSON-seguros."""
        if self.window is None:
            return
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        try:
            self.window.evaluate_js(f"{fn}({payload})")
        except Exception:
            pass

    # -- conversaciones -----------------------------------------------------

    def listar_conversaciones(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.gestor.listar_conversaciones():
            out.append({
                "nombre": c["path"].name,
                "titulo": c.get("titulo") or c["path"].name,
                "mensajes": c.get("mensajes", 0),
                "crew": c["path"].name.startswith("crew-"),
            })
        return out

    def _chat(self, nombre: str) -> ConversacionChat:
        chat = self._chats.get(nombre)
        if chat is None:
            d = self.gestor.cargar_conversacion(nombre)
            if d is None:
                raise ValueError(f"no existe la conversación {nombre}")
            chat = ConversacionChat(d)
            self._chats[nombre] = chat
        return chat

    def cargar_conversacion(self, nombre: str) -> Dict[str, Any]:
        chat = self._chat(nombre)
        mensajes = []
        for m in chat.mensajes:
            rol = m.get("role")
            if rol not in ("user", "assistant"):
                continue                       # tool calls, etc.: no se muestran
            contenido = m.get("content") or ""
            if not contenido:
                continue
            mensajes.append({"rol": rol, "html": md_a_html(contenido)})
        return {"nombre": nombre,
                "titulo": chat.meta.get("titulo", nombre),
                "mensajes": mensajes}

    def nueva_conversacion(self, titulo: str = "") -> str:
        d = self.gestor.crear_conversacion((titulo or "").strip())
        return d.name

    # -- enviar (con streaming) --------------------------------------------

    def enviar(self, nombre: str, texto: str) -> Dict[str, Any]:
        """Dispara el turno en un hilo y streamea por evaluate_js. Devuelve ya."""
        texto = (texto or "").strip()
        if not texto:
            return {"ok": False}
        threading.Thread(target=self._worker_enviar, args=(nombre, texto),
                         daemon=True).start()
        return {"ok": True}

    def _worker_enviar(self, nombre: str, texto: str):
        try:
            chat = self._chat(nombre)
        except Exception:
            self._js("chatError", traceback.format_exc())
            return
        # Eco del mensaje del usuario + apertura de la burbuja del agente.
        self._js("chatUsuario", md_a_html(texto))
        self._js("chatInicioRespuesta")
        try:
            respuesta = chat.enviar(texto, al_fragmento=lambda t: self._js("chatFragmento", t))
            self._js("chatFinRespuesta", md_a_html(respuesta))
        except Exception:
            self._js("chatError", traceback.format_exc())

    # -- configuración ------------------------------------------------------

    def config_campos(self) -> List[Dict[str, Any]]:
        """Los campos editables del .env, con los secretos enmascarados."""
        from core import config
        valores = config.leer()
        campos = []
        for clave, etiqueta, secreto in config.CAMPOS:
            v = valores.get(clave, "")
            campos.append({
                "clave": clave, "etiqueta": etiqueta, "secreto": bool(secreto),
                "valor": config.enmascarar(v) if secreto else v,
                "tiene": bool(v),
            })
        return campos

    def guardar_config(self, cambios: Dict[str, str]) -> Dict[str, Any]:
        """Guarda SOLO los campos que mandó el front (los que el usuario tocó),
        preservando el resto del .env."""
        from core import config
        try:
            config.guardar({k: v for k, v in (cambios or {}).items()})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def probar_proveedor(self, proveedor: str) -> Dict[str, Any]:
        """Hace una llamada real al proveedor para confirmar que la key sirve."""
        from core import config
        try:
            return config.probar((proveedor or "").strip())
        except Exception as e:
            return {"ok": False, "error": str(e)}
