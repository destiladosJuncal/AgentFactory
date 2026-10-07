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
from pathlib import Path
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
                "creado": c.get("creado", ""),
                "actualizado": c.get("actualizado", c.get("creado", "")),
            })
        # Más recientes primero: la lista queda como un registro cronológico.
        out.sort(key=lambda x: x.get("actualizado") or "", reverse=True)
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
            ts = m.get("ts", "")
            if rol == "user":
                items.append({"tipo": "user", "ts": ts, "html": md_a_html(m.get("content") or "")})
            elif rol == "assistant":
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", {})
                    meta_tool[tc.get("id")] = {"nombre": fn.get("name", "herramienta"),
                                               "args": fn.get("arguments", "")}
                cont = (m.get("content") or "").strip()
                if cont:
                    items.append({"tipo": "assistant", "ts": ts, "html": md_a_html(cont)})
            elif rol == "tool":
                meta = meta_tool.get(m.get("tool_call_id"), {})
                items.append({
                    "tipo": "tool", "ts": ts,
                    "nombre": meta.get("nombre", "herramienta"),
                    "args": meta.get("args", ""),
                    "resultado": m.get("content") or "",
                })
        return {"nombre": nombre,
                "titulo": chat.meta.get("titulo", nombre),
                "creado": chat.meta.get("creado", ""),
                "actualizado": chat.meta.get("actualizado", chat.meta.get("creado", "")),
                "mensajes": items}

    def nueva_conversacion(self, titulo: str = "") -> str:
        d = self.gestor.crear_conversacion((titulo or "").strip())
        return d.name

    def arte_bienvenida(self) -> Dict[str, Any]:
        """El logo de arranque. En la web se usa el PNG de la marca (queda mejor
        que el ASCII); si no está, se cae al logo ASCII de core/bienvenida.py."""
        import base64
        from core.rutas import dir_app
        raiz = dir_app()
        for nombre in ("agentfactory-icon-1024.png", "agentfactory-icon.png", "icono.png"):
            p = raiz / nombre
            if p.exists():
                try:
                    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                    return {"img": f"data:image/png;base64,{b64}"}
                except Exception:
                    pass
        from core import bienvenida
        return {"logo": bienvenida.LOGO.strip("\n")}

    def nueva_desde_texto(self, texto: str) -> str:
        """Crea una conversación tomando el título del primer mensaje (igual que
        la app vieja: empezás a escribir y te pone en una conversación nueva con
        título, sin tener que crearla a mano)."""
        from core import bienvenida
        titulo = bienvenida.title_from_text(texto or "")
        d = self.gestor.crear_conversacion(titulo)
        return d.name

    def detener_chat(self, nombre: str) -> Dict[str, Any]:
        """Frena el turno en curso de esa conversación (cancelación cooperativa:
        corta el loop de herramientas; una llamada al modelo ya emitida termina)."""
        chat = self._chats.get(nombre)
        if chat is not None:
            chat.cancelado = True
        return {"ok": True}

    def borrar_conversacion(self, nombre: str) -> Dict[str, Any]:
        """Borra la conversación (su carpeta). No se puede deshacer."""
        import shutil
        d = self.gestor.cargar_conversacion(nombre)
        if d is None:
            return {"ok": False, "error": "no existe"}
        try:
            shutil.rmtree(d, ignore_errors=True)
            self._chats.pop(nombre, None)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def abrir_carpeta_conversacion(self, nombre: str) -> Dict[str, Any]:
        """Abre la carpeta de la conversación en el explorador del sistema."""
        from core import plataforma
        d = self.gestor.cargar_conversacion(nombre)
        if d is None:
            return {"ok": False, "error": "no existe la conversación"}
        err = plataforma.abrir_carpeta(d)
        return {"ok": not err, "error": err}

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

    # Mensaje que se manda al auto-continuar (igual que la app vieja).
    _TEXTO_CONTINUAR = (
        "Sí, seguí con lo que propusiste. Si en realidad necesitabas que "
        "eligiera entre alternativas, no asumas: pará y preguntame concreto.")

    def continuar_turno(self, nombre: str) -> Dict[str, Any]:
        """Manda la respuesta de continuación (del botón '▶ Sí, seguí' o del
        'Sí a todo' automático)."""
        threading.Thread(target=self._worker_enviar,
                         args=(nombre, self._TEXTO_CONTINUAR, "▶ (sí, seguí)"),
                         daemon=True).start()
        return {"ok": True}

    def _worker_enviar(self, nombre: str, texto: str, etiqueta: str = None):
        try:
            chat = self._chat(nombre)
        except Exception:
            self._js("chatError", nombre, traceback.format_exc())
            return
        from core.chat import parece_pausa
        # Todos los eventos llevan el nombre de la conversación, para que el front
        # pinte cada stream en SU conversación (y no en la que estés mirando) y
        # para que Enviar/Detener reflejen el estado de cada una por separado.
        self._js("chatUsuario", nombre, md_a_html(etiqueta or texto))
        self._js("chatInicioRespuesta", nombre)
        try:
            respuesta = chat.enviar(
                texto, al_fragmento=lambda t: self._js("chatFragmento", nombre, t))
            # chatFin lleva `pausa`: True si el agente cortó preguntando (para el
            # 'Sí a todo' / el botón de continuar). Recarga la conversación para
            # mostrar las ejecuciones además del texto final.
            self._js("chatFin", nombre, bool(parece_pausa(respuesta)))
        except Exception:
            self._js("chatError", nombre, traceback.format_exc())

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

    # -- empaquetar / distribuir -------------------------------------------

    def crear_paquete(self, formato_pkg: str) -> Dict[str, Any]:
        """Arma un paquete para compartir: 'zip' (portable, multiplataforma) o
        'dmg' (instalador de Mac). Lo guarda en ~/Downloads y devuelve la ruta.

        (No usa el diálogo nativo de "guardar" a propósito: llamado desde el hilo
        del bridge, en macOS ese modal se cuelga. Guardar en una carpeta conocida
        y abrirla es más robusto.)"""
        from core import empaquetar
        es_dmg = (formato_pkg == "dmg")
        sugerido = "AgentFactory-0.2.dmg" if es_dmg else "AgentFactory-portable.zip"
        carpeta = Path.home() / "Downloads"
        if not carpeta.is_dir():
            carpeta = Path.home()
        destino = carpeta / sugerido

        if es_dmg:
            app = destino.parent / "AgentFactory.app"
            r = empaquetar.construir_app(app)
            if "error" in r:
                return {"ok": False, "error": r["error"],
                        "detalle": r.get("faltan") or r.get("hallazgos")}
            d = empaquetar.construir_dmg(destino, app)
            if "error" in d:
                return {"ok": False, "error": d["error"]}
            self._abrir_carpeta_de(destino)
            return {"ok": True, "ruta": d["dmg"], "bytes": d.get("bytes", 0)}

        r = empaquetar.crear_zip(destino)
        if "error" in r:
            return {"ok": False, "error": r["error"], "detalle": r.get("hallazgos")}
        self._abrir_carpeta_de(destino)
        return {"ok": True, "ruta": str(destino), "bytes": r.get("bytes", 0)}

    @staticmethod
    def _abrir_carpeta_de(archivo: Path):
        try:
            from core import plataforma
            plataforma.abrir_carpeta(Path(archivo).parent)
        except Exception:
            pass

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

    def captura_sitios(self) -> Dict[str, Any]:
        """Dominios capturados (agrupados por dominio registrable) con su conteo."""
        from core import proxy_tool
        r = proxy_tool.listar_sitios_capturados()
        return {"sitios": r.get("sitios", []), "aviso": r.get("aviso")}

    def captura_urls(self, sitio: str) -> Dict[str, Any]:
        """Las URLs capturadas de un dominio (host + ruta), sin repetir."""
        from core import proxy_tool
        r = proxy_tool.buscar_en_captura(sitio=sitio, limite=400)
        out, vistos = [], set()
        for f in r.get("flujos", []):
            url = (f.get("sitio") or "") + (f.get("ruta") or "")
            clave = (f.get("metodo"), url)
            if clave in vistos:
                continue
            vistos.add(clave)
            out.append({"metodo": f.get("metodo") or "", "url": url,
                        "estado": f.get("estado"),
                        "tipo": (f.get("tipo") or "").split(";")[0]})
        return {"urls": out[:400], "total": r.get("total", len(out))}

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
