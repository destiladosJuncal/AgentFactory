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
        self._room = None             # enjambre activo
        self._enjambre_cancelado = False
        self._proxy = None            # captura (mitmproxy)
        self._almacen = None

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
        """Items de la conversación, incluidas las EJECUCIONES locales (shell,
        biblioteca, paquetes, etc.): se muestran como bloques de herramienta con
        su resultado, igual que en la app de escritorio."""
        chat = self._chat(nombre)
        meta_tool: Dict[str, Dict[str, str]] = {}   # id -> {nombre, args}
        items = []
        for m in chat.mensajes:
            rol = m.get("role")
            if rol == "user":
                items.append({"tipo": "user", "html": md_a_html(m.get("content") or "")})
            elif rol == "assistant":
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", {})
                    meta_tool[tc.get("id")] = {"nombre": fn.get("name", "herramienta"),
                                               "args": fn.get("arguments", "")}
                cont = (m.get("content") or "").strip()
                if cont:
                    items.append({"tipo": "assistant", "html": md_a_html(cont)})
            elif rol == "tool":
                meta = meta_tool.get(m.get("tool_call_id"), {})
                items.append({
                    "tipo": "tool",
                    "nombre": meta.get("nombre", "herramienta"),
                    "args": meta.get("args", ""),
                    "resultado": m.get("content") or "",
                })
        return {"nombre": nombre,
                "titulo": chat.meta.get("titulo", nombre),
                "mensajes": items}

    def nueva_conversacion(self, titulo: str = "") -> str:
        d = self.gestor.crear_conversacion((titulo or "").strip())
        return d.name

    def renombrar_conversacion(self, nombre: str, titulo: str) -> Dict[str, Any]:
        """Cambia el título visible (en meta.json). No toca la carpeta ni el
        historial, así que no rompe nada que apunte al slug."""
        titulo = (titulo or "").strip()
        if not titulo:
            return {"ok": False}
        try:
            chat = self._chat(nombre)
            chat.meta["titulo"] = titulo
            chat.meta_path.write_text(
                json.dumps(chat.meta, indent=2, ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "titulo": titulo}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
            chat.enviar(texto, al_fragmento=lambda t: self._js("chatFragmento", t))
            # Al terminar se recarga toda la conversación: así aparecen las
            # ejecuciones (shell, biblioteca, paquetes…) que ocurrieron en el
            # medio, no solo el texto final.
            self._js("chatFin", nombre)
        except Exception:
            self._js("chatError", traceback.format_exc())

    # -- ejecutar código del chat ------------------------------------------

    def ejecutar_codigo(self, codigo: str, lenguaje: str = "",
                        confirmado: bool = False) -> Dict[str, Any]:
        """Corre un bloque de código del chat. Mantiene el guard de seguridad:
        si el código toca/borra archivos o lee credenciales, NO corre hasta que
        la persona confirme (necesita_confirmacion → el front muestra el aviso y
        vuelve a llamar con confirmado=True)."""
        from core import ejecucion
        es_py = (lenguaje or "").lower() in ("python", "py", "python3")
        riesgos = ejecucion.analizar_riesgo(codigo or "", es_python=es_py)
        if riesgos and not confirmado:
            return {"necesita_confirmacion": True,
                    "riesgos": [expl for _, expl in riesgos]}
        anterior = ejecucion.CONFIRMADOR
        if riesgos:
            # Ya lo aprobó en la UI: permitir esta corrida.
            ejecucion.CONFIRMADOR = lambda resumen, detalle, clave: "permitir"
        try:
            r = ejecucion.ejecutar_python(codigo) if es_py else ejecucion.ejecutar_shell(codigo)
        except Exception as e:
            r = {"stderr": str(e), "codigo_retorno": -1}
        finally:
            ejecucion.CONFIRMADOR = anterior
        return {"ok": True, "resultado": r}

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

    # -- enjambre (multi-agente) -------------------------------------------

    def _emitir_msg(self, m):
        self._js("enjambreMensaje",
                 {"speaker": m.speaker, "text": m.text, "audience": m.audience})

    def enjambre_iniciar(self, tarea: str, max_agents: int = 4,
                         con_tools: bool = False) -> Dict[str, Any]:
        tarea = (tarea or "").strip()
        if not tarea:
            return {"ok": False}
        self._enjambre_cancelado = False
        self._room = None
        threading.Thread(target=self._worker_enjambre_kickoff,
                         args=(tarea, int(max_agents), bool(con_tools)),
                         daemon=True).start()
        return {"ok": True}

    def _worker_enjambre_kickoff(self, tarea, maxi, con_tools):
        try:
            from core import crew as crewmod
            self._js("enjambreEstado", "🧠 Planificando equipo…")
            specs = crewmod.plan_team(tarea, max_agents=maxi)
            self._js("enjambreEquipo",
                     [{"name": s.name, "role": s.role, "goal": s.goal} for s in specs])
            agentes = crewmod.build_crew(specs, tools_enabled=con_tools).agents
            self._room = crewmod.Room(agentes)
            self._room.kickoff(tarea, progress=self._emitir_msg,
                               should_stop=lambda: self._enjambre_cancelado)
            self._js("enjambreFin")
        except Exception:
            self._js("enjambreError", traceback.format_exc())

    def enjambre_mensaje(self, texto: str) -> Dict[str, Any]:
        if self._room is None:
            return {"ok": False}
        self._enjambre_cancelado = False
        threading.Thread(target=self._worker_enjambre_msg, args=(texto,),
                         daemon=True).start()
        return {"ok": True}

    def _worker_enjambre_msg(self, texto):
        try:
            self._room.user_message(texto, progress=self._emitir_msg,
                                    should_stop=lambda: self._enjambre_cancelado)
            self._js("enjambreFin")
        except Exception:
            self._js("enjambreError", traceback.format_exc())

    def enjambre_detener(self) -> Dict[str, Any]:
        self._enjambre_cancelado = True
        if self._room is not None:
            for a in self._room.agents:
                try:
                    a.cancel()
                except Exception:
                    pass
        return {"ok": True}

    # -- captura (proxy + Firefox) -----------------------------------------

    def abrir_firefox(self) -> Dict[str, Any]:
        """Arranca el proxy (si no está) y abre el Firefox de captura. Un clic
        hace todo, igual que en la app de escritorio."""
        from core import proxy as proxymod
        try:
            import mitmproxy  # noqa: F401
        except ImportError:
            return {"ok": False, "error":
                    "mitmproxy no está instalado. Instalalo con: pip install mitmproxy"}
        try:
            puerto = proxymod.PUERTO_DEFAULT
            if self._proxy is None or not getattr(self._proxy, "corriendo", False):
                if self._almacen is None:
                    self._almacen = proxymod.Almacen(proxymod.dir_proxy() / "sesion.db")
                self._proxy = proxymod.Proxy(self._almacen, puerto=puerto,
                                             al_flujo=lambda i: None)
                err = self._proxy.iniciar()
                if err:
                    self._proxy = None
                    return {"ok": False, "error": f"no pude iniciar el proxy: {err}"}
            r = proxymod.lanzar_firefox(puerto)
            if isinstance(r, dict) and r.get("error"):
                return {"ok": False, "error": r["error"]}
            return {"ok": True, "puerto": puerto}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def captura_estado(self) -> Dict[str, Any]:
        from core import proxy_tool, formato
        b = proxy_tool.tamano_captura()
        corriendo = bool(self._proxy is not None and getattr(self._proxy, "corriendo", False))
        return {"bytes": b, "texto": formato.size(b) if b else "vacía",
                "corriendo": corriendo}

    def vaciar_captura(self) -> Dict[str, Any]:
        from core import proxy_tool
        return proxy_tool.vaciar_captura()

    # -- tareas programadas -------------------------------------------------

    _EMOJI_ESTADO = {"programada": "✅", "ausente": "⏸", "desconocido": "❓"}

    def tareas_listar(self) -> List[Dict[str, Any]]:
        from core import programador
        out = []
        for t in programador.listar_tareas():
            estado = programador.estado_tarea(t["id"])
            out.append({
                "id": t["id"],
                "titulo": t.get("titulo", "Tarea"),
                "cuando": programador.describir(t),
                "tipo": t.get("tipo", "agente"),
                "estado": estado,
                "emoji": self._EMOJI_ESTADO.get(estado, "❓"),
                "ultima": t.get("ultima_corrida"),
            })
        return out

    def tarea_corridas(self, tarea_id: str) -> List[Dict[str, Any]]:
        from core import programador
        return programador.corridas(tarea_id)[:10]

    def tarea_correr(self, tarea_id: str) -> Dict[str, Any]:
        from core import programador
        err = programador.correr_ahora(tarea_id)
        return {"ok": not err, "error": err}

    def tarea_recargar(self, tarea_id: str) -> Dict[str, Any]:
        from core import programador
        return programador.recargar(tarea_id)

    def tarea_borrar(self, tarea_id: str) -> Dict[str, Any]:
        from core import programador
        try:
            programador.borrar_tarea(tarea_id)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
