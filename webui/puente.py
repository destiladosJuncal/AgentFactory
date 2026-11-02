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
        self._workers_activos = []    # chats de workers del orquestador en vuelo
        self._orq_sesiones = {}       # run_id -> {state, history} (Fase A/B vivas)
        self._enjambre_cancelado = False
        self._proxy = None            # captura (mitmproxy)
        self._almacen = None
        # Guardián: cuando el agente quiere correr algo destructivo, el core nos
        # pide confirmación desde el hilo de trabajo. Lo mostramos como ventana
        # modal en la UI y bloqueamos el hilo hasta que la persona decide.
        self._confirmaciones: Dict[str, Any] = {}
        self._confirm_lock = threading.Lock()
        self._confirm_seq = 0
        from core import ejecucion
        ejecucion.registrar_confirmador(self._confirmar_desde_hilo)

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
            titulo = c.get("titulo") or c["path"].name
            # El enjambre es autocontenido: las conversaciones de workers (y las
            # 'crew ·' viejas) NO ensucian la lista del Chat; viven en la ventana
            # de Agentes / bajo la carpeta del run.
            if titulo.startswith("worker ·") or titulo.startswith("crew ·"):
                continue
            out.append({
                "nombre": c["path"].name,
                "titulo": titulo,
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
                    items.append({"tipo": "assistant", "ts": ts, "html": md_a_html(cont),
                                  "segundos": m.get("segundos")})
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
        self._resolver_confirmaciones_pendientes()
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

    # ---- Comprimir conversaciones viejas -----------------------------------
    @staticmethod
    def _fecha_conv(c: Dict[str, Any]):
        """Devuelve un datetime de la conversación (actualizado o creado), o None."""
        from datetime import datetime
        txt = (c.get("actualizado") or c.get("creado") or "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(txt[:len(fmt) + 2], fmt)
            except Exception:
                continue
        return None

    @staticmethod
    def _tam_carpeta(d: Path) -> int:
        total = 0
        for f in d.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except Exception:
                pass
        return total

    def _viejas(self, dias: int) -> List[Dict[str, Any]]:
        """Conversaciones cuyo 'actualizado' es más viejo que `dias` días."""
        from datetime import datetime, timedelta
        limite = datetime.now() - timedelta(days=max(0, int(dias)))
        out = []
        for c in self.listar_conversaciones():
            f = self._fecha_conv(c)
            if f is not None and f < limite:
                out.append(c)
        return out

    def conversaciones_viejas(self, dias: int = 90) -> Dict[str, Any]:
        """Vista previa: cuántas conversaciones se comprimirían y cuánto pesan."""
        from core import formato
        viejas = self._viejas(dias)
        total = 0
        for c in viejas:
            d = self.gestor.cargar_conversacion(c["nombre"])
            if d is not None:
                total += self._tam_carpeta(d)
        return {"cantidad": len(viejas), "bytes": total, "legible": formato.size(total)}

    def comprimir_viejas(self, dias: int = 90) -> Dict[str, Any]:
        """Archiva en un .zip (y saca de la lista) las conversaciones más viejas
        que `dias` días. El zip queda en <datos>/_archivo/ como respaldo; no se
        pierde nada, solo se libera espacio y se despeja la lista."""
        import shutil
        import zipfile
        from datetime import datetime
        from core import formato, plataforma
        viejas = self._viejas(dias)
        if not viejas:
            return {"ok": True, "cantidad": 0, "legible": formato.size(0)}
        dir_archivo = self.gestor.base_dir.parent / "_archivo"
        dir_archivo.mkdir(parents=True, exist_ok=True)
        sello = datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_path = dir_archivo / f"conversaciones-{sello}.zip"
        liberado = 0
        archivadas = 0
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for c in viejas:
                    d = self.gestor.cargar_conversacion(c["nombre"])
                    if d is None:
                        continue
                    liberado += self._tam_carpeta(d)
                    for f in d.rglob("*"):
                        if f.is_file():
                            z.write(f, Path(c["nombre"]) / f.relative_to(d))
                    archivadas += 1
            # Recién después de escribir el zip OK, borro los originales.
            for c in viejas:
                d = self.gestor.cargar_conversacion(c["nombre"])
                if d is not None:
                    shutil.rmtree(d, ignore_errors=True)
                    self._chats.pop(c["nombre"], None)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        plataforma.abrir_carpeta(dir_archivo)
        return {
            "ok": True,
            "cantidad": archivadas,
            "bytes": liberado,
            "legible": formato.size(liberado),
            "ruta": str(zip_path),
        }

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
                texto,
                al_fragmento=lambda t: self._js("chatFragmento", nombre, t),
                al_evento=lambda ev: self._js("chatHerramienta", nombre, ev))
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

    # -- guardián de confirmaciones (el agente pide permiso) ---------------

    def _confirmar_desde_hilo(self, resumen: str, detalle: str, clave: str) -> str:
        """Lo llama core.ejecucion desde el hilo de trabajo cuando el agente va a
        correr algo que no se puede deshacer. Muestra la ventana de aprobación y
        bloquea el hilo hasta que la persona responde (tope de 5 min por las
        dudas, para no dejar el turno colgado para siempre)."""
        if self.window is None:
            return "no"
        evento, caja = threading.Event(), []
        with self._confirm_lock:
            self._confirm_seq += 1
            cid = f"c{self._confirm_seq}"
            self._confirmaciones[cid] = (evento, caja)
        from core import plataforma
        plataforma.notificar("AgentFactory · se requiere tu aprobación",
                             f"El agente quiere {resumen}")
        self._js("confirmarPedir", cid, resumen, detalle, clave)
        if not evento.wait(timeout=300):
            self._js("confirmarCerrar", cid)
            with self._confirm_lock:
                self._confirmaciones.pop(cid, None)
            return "no"
        with self._confirm_lock:
            self._confirmaciones.pop(cid, None)
        return caja[0] if caja else "no"

    def notificar(self, titulo: str, texto: str) -> Dict[str, Any]:
        """Notificación del sistema que dispara el front cuando el agente queda
        esperando una acción de la persona (p. ej. una pausa preguntando)."""
        from core import plataforma
        plataforma.notificar(titulo or "AgentFactory", texto or "")
        return {"ok": True}

    def confirmar_responder(self, cid: str, decision: str) -> Dict[str, Any]:
        """El front devuelve la decisión (permitir / siempre / no)."""
        with self._confirm_lock:
            par = self._confirmaciones.get(cid)
        if par is None:
            return {"ok": False}
        evento, caja = par
        caja.append(decision if decision in ("permitir", "siempre") else "no")
        evento.set()
        return {"ok": True}

    def _resolver_confirmaciones_pendientes(self):
        """Si se detiene el turno, no dejamos ninguna confirmación colgada."""
        with self._confirm_lock:
            pendientes = list(self._confirmaciones.items())
        for cid, (evento, caja) in pendientes:
            if not caja:
                caja.append("no")
            evento.set()
            self._js("confirmarCerrar", cid)

    # -- configuración ------------------------------------------------------

    def config_campos(self) -> List[Dict[str, Any]]:
        """Los campos editables del .env, con los secretos enmascarados."""
        from core import config
        valores = config.leer()
        campos = []
        sugerencias = getattr(config, "SUGERENCIAS", {})
        for clave, etiqueta, secreto in config.CAMPOS:
            v = valores.get(clave, "")
            sug = sugerencias.get(clave, {})
            campos.append({
                "clave": clave, "etiqueta": etiqueta, "secreto": bool(secreto),
                "valor": config.enmascarar(v) if secreto else v,
                "tiene": bool(v),
                "opciones": sug.get("opciones", []),
                "default": sug.get("default", ""),
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

    # Proveedores LLM: (clave interna, variable de key en el .env, etiqueta)
    _PROVEEDORES_LLM = [
        ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek"),
        ("claude", "ANTHROPIC_API_KEY", "Claude"),
        ("qwen", "QWEN_API_KEY", "Qwen"),
        ("gemini", "GEMINI_API_KEY", "Gemini"),
    ]

    def probar_todo(self) -> Dict[str, Any]:
        """Prueba TODO lo que tenga credenciales cargadas: una llamada real por
        cada proveedor LLM con key, más Jev si está configurado. Devuelve un
        resultado por proveedor."""
        from core import config
        valores = config.leer()
        resultados: List[Dict[str, Any]] = []
        for prov, keyvar, etiqueta in self._PROVEEDORES_LLM:
            if not (valores.get(keyvar) or "").strip():
                continue
            r = config.probar(prov)
            resultados.append({
                "proveedor": etiqueta,
                "ok": bool(r.get("ok")),
                "modelo": r.get("modelo", ""),
                "detalle": (r.get("respuesta") if r.get("ok")
                            else (r.get("detalle") or r.get("error") or "falló")),
                "pista": r.get("pista", ""),
            })
        if (valores.get("TYPESAFE_API_KEY") or "").strip():
            resultados.append(self._probar_jev())
        if not resultados:
            return {"ok": False, "resultados": [],
                    "error": "No hay ninguna credencial cargada para probar."}
        return {"ok": all(x["ok"] for x in resultados), "resultados": resultados}

    @staticmethod
    def _probar_jev() -> Dict[str, Any]:
        """Jev no es un LLM: lo probamos con una clasificación booleana mínima."""
        import os
        from core import classifier
        modelo = os.getenv("TYPESAFE_MODEL") or classifier.TYPESAFE_MODEL_DEFAULT
        try:
            ans = classifier.jev_query(
                "ping", {"prueba": classifier.boolean("¿Esto es una prueba de conexión?")})
            a = ans.get("prueba", {})
            return {"proveedor": "Jev", "ok": True, "modelo": modelo,
                    "detalle": f"respondió (prob {a.get('probability')})", "pista": ""}
        except Exception as e:
            return {"proveedor": "Jev", "ok": False, "modelo": modelo,
                    "detalle": str(e)[:300], "pista": ""}

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


    # -- Orquestador en DOS FASES (A: planificar conversando · B: ejecutar) ---
    # Cada corrida vive en su carpeta (_orquestaciones/<run_id>): workers y estado
    # quedan autocontenidos, fuera del Chat. _orq_sesiones guarda las sesiones
    # vivas (estado + historial de la charla de planificación).

    def orq_iniciar(self, tarea: str, fraccionar: bool = True) -> Dict[str, Any]:
        """Fase A: clasifica (router Jev), elige el modelo orquestador por
        categoría y arranca la planificación conversacional. No ejecuta nada."""
        tarea = (tarea or "").strip()
        if not tarea:
            return {"ok": False}
        self._enjambre_cancelado = False
        threading.Thread(target=self._worker_orq_iniciar,
                         args=(tarea, bool(fraccionar)), daemon=True).start()
        return {"ok": True}

    def _worker_orq_iniciar(self, tarea, fraccionar):
        try:
            from core import classifier, planning
            from core.orchestration_state import OrchestrationState
            self._js("orqEvento", {"type": "estado", "texto": "🔎 Clasificando el pedido…"})
            cls = classifier.classify_task(tarea)
            prov = planning.orchestrator_provider_for(cls.get("category"))
            state = OrchestrationState.create(tarea, classification=cls, orchestrator_model=prov)
            self._orq_sesiones[state.run_id] = {
                "state": state, "history": [{"role": "user", "content": tarea}]}
            self._js("orqEvento", {"type": "run", "run_id": state.run_id,
                                   "tarea": tarea, "clasificacion": cls, "modelo": prov})
            self._orq_planificar_vuelta(state.run_id)
        except Exception:
            self._js("orqError", traceback.format_exc())

    def _orq_planificar_vuelta(self, run_id):
        from core import planning
        from core.orchestration_state import AWAITING_APPROVAL, PLANNING
        ses = self._orq_sesiones.get(run_id)
        if not ses:
            return
        state = ses["state"]
        cls = state.classification
        llm = planning.make_orchestrator_llm(
            cls.get("category"),
            on_fallback=lambda: self._js("orqEvento", {"type": "fallback", "run_id": run_id}))
        on_token = lambda t: self._js("orqEvento", {
            "type": "think", "run_id": run_id, "phase": "plan", "text": t})
        self._js("orqEvento", {"type": "estado", "texto": "🧠 Planificando…"})
        try:
            r = planning.plan_turn(ses["history"], cls, llm=llm, on_token=on_token)
        except Exception as e:
            self._js("orqError", str(e)); return
        ses["history"].append({"role": "assistant", "content": r.get("reply", "")})
        if r["kind"] == "questions":
            state.status = PLANNING; state.save()
            self._js("orqEvento", {"type": "questions", "run_id": run_id,
                                   "questions": r["questions"], "note": r.get("note", "")})
        else:
            state.set_plan(r["subtasks"]); state.status = AWAITING_APPROVAL; state.save()
            self._js("orqEvento", {"type": "plan", "run_id": run_id,
                                   "subtasks": [s.as_dict() for s in state.subtasks],
                                   "note": r.get("note", "")})

    def orq_responder(self, run_id: str, texto: str) -> Dict[str, Any]:
        """Continúa la charla de planificación (responder preguntas / ajustar)."""
        ses = self._orq_sesiones.get(run_id)
        if not ses or not (texto or "").strip():
            return {"ok": False}
        ses["history"].append({"role": "user", "content": texto.strip()})
        threading.Thread(target=self._orq_planificar_vuelta, args=(run_id,), daemon=True).start()
        return {"ok": True}

    def orq_editar_plan(self, run_id: str, subtasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Reemplaza el plan con las ediciones manuales del panel."""
        from core.orchestration_state import SubTask, AWAITING_APPROVAL
        ses = self._orq_sesiones.get(run_id)
        if not ses:
            return {"ok": False}
        state = ses["state"]
        state.set_plan([SubTask.from_dict(s) for s in (subtasks or [])])
        state.status = AWAITING_APPROVAL; state.save()
        return {"ok": True, "subtasks": [s.as_dict() for s in state.subtasks]}

    def orq_confirmar_scope(self, run_id: str, ok: bool = True) -> Dict[str, Any]:
        ses = self._orq_sesiones.get(run_id)
        if not ses:
            return {"ok": False}
        ses["state"].scope_confirmed = bool(ok); ses["state"].save()
        return {"ok": True}

    def orq_ejecutar(self, run_id: str, supervisar: bool = True) -> Dict[str, Any]:
        """Fase B: con el plan aprobado, corre los workers. Si la categoría pide
        scope y no está confirmado, frena y lo pide. `supervisar` activa el
        steering (el orquestador corrige/redirige workers mal encaminados)."""
        ses = self._orq_sesiones.get(run_id)
        if not ses:
            return {"ok": False}
        cls = ses["state"].classification
        if cls.get("needs_scope") and not ses["state"].scope_confirmed:
            return {"ok": False, "needs_scope": True, "run_id": run_id,
                    "categoria": cls.get("category")}
        self._enjambre_cancelado = False
        self._workers_activos = []
        threading.Thread(target=self._worker_orq_ejecutar,
                         args=(run_id, bool(supervisar)), daemon=True).start()
        return {"ok": True}

    def _worker_orq_ejecutar(self, run_id, supervisar=True):
        try:
            from core import orchestrator as orq
            from core import planning
            from core.orchestration_state import RUNNING, DONE, ERROR, runs_dir
            ses = self._orq_sesiones.get(run_id)
            if not ses:
                return
            state = ses["state"]; cls = state.classification
            state.status = RUNNING; state.save()

            def ev_worker(wid, kind, data):
                tipo = {"think": "worker_think", "steer": "worker_steer"}.get(kind, "worker_tool")
                clave = {"think": "text", "steer": "steer"}.get(kind, "ev")
                self._js("orqEvento", {"type": tipo, "run_id": run_id, "id": wid, clave: data})

            # review/síntesis + supervisión con el modelo por categoría (fallback incluido).
            _mo = planning.make_orchestrator_llm(cls.get("category"))
            plan_llm = lambda prompt, on_token=None: _mo(
                [{"role": "user", "content": prompt}], on_token)

            workers_dir = runs_dir() / run_id / "workers"
            run_worker = orq.deepseek_worker_runner(
                tools=True, registry=self._workers_activos, on_event=ev_worker,
                base_dir=workers_dir,
                supervisor=(_mo if supervisar else None), max_corrections=2)

            def prog(ev):
                # En Fase B el panel del plan ya existe; el 'plan'/'start' que
                # emite el motor al arrancar haría que la UI vuelva a modo Fase A.
                if ev.get("type") not in ("plan", "start"):
                    self._js("orqEvento", {**ev, "run_id": run_id})
                try:
                    state.save()
                except Exception:
                    pass

            motor = orq.Orchestrator(plan_llm, run_worker, progress=prog,
                                     should_stop=lambda: self._enjambre_cancelado)
            sintesis = motor.run(state.task, subtasks=state.subtasks)
            state.synthesis = sintesis
            state.status = RUNNING if self._enjambre_cancelado else DONE
            state.save()
            self._js("orqEvento", {"type": "fin", "run_id": run_id, "sintesis": sintesis})
        except Exception:
            try:
                ses = self._orq_sesiones.get(run_id)
                if ses:
                    ses["state"].status = ERROR; ses["state"].save()
            except Exception:
                pass
            self._js("orqError", traceback.format_exc())

    def orq_listar(self) -> List[Dict[str, Any]]:
        """Resumen de corridas (para el árbol de enjambres)."""
        from core.orchestration_state import list_runs
        return list_runs()

    def orq_cargar(self, run_id: str) -> Dict[str, Any]:
        """Estado completo de una corrida (para desplegar el árbol)."""
        from core.orchestration_state import OrchestrationState
        ses = self._orq_sesiones.get(run_id)
        st = ses["state"] if ses else OrchestrationState.load(run_id)
        return st.as_dict() if st else {}

    def orq_reanudar(self, run_id: str, supervisar: bool = True) -> Dict[str, Any]:
        """Reanuda una corrida interrumpida: carga el estado, repone a 'pending'
        las que quedaron a mitad ('running'), conserva las 'done', y sigue la
        ejecución desde ahí (el motor saltea las ya hechas)."""
        from core.orchestration_state import OrchestrationState, PENDING, IN_PROGRESS
        st = OrchestrationState.load(run_id)
        if not st:
            return {"ok": False, "error": "no encontré esa corrida"}
        for s in st.subtasks:
            if s.status == IN_PROGRESS:       # worker interrumpido -> re-correr
                s.status = PENDING
        st.save()
        self._orq_sesiones[run_id] = {"state": st,
                                      "history": [{"role": "user", "content": st.task}]}
        cls = st.classification
        if cls.get("needs_scope") and not st.scope_confirmed:
            return {"ok": False, "needs_scope": True, "run_id": run_id,
                    "categoria": cls.get("category"), "estado": st.as_dict()}
        self._enjambre_cancelado = False
        self._workers_activos = []
        threading.Thread(target=self._worker_orq_ejecutar,
                         args=(run_id, bool(supervisar)), daemon=True).start()
        return {"ok": True, "run_id": run_id, "estado": st.as_dict()}

    def orq_detener(self) -> Dict[str, Any]:
        self._enjambre_cancelado = True
        # Deja la corrida en 'paused' para poder reanudarla.
        for ses in self._orq_sesiones.values():
            try:
                from core.orchestration_state import RUNNING, PAUSED
                if ses["state"].status == RUNNING:
                    ses["state"].status = PAUSED; ses["state"].save()
            except Exception:
                pass
        for chat in getattr(self, "_workers_activos", []):
            try:
                chat.cancelado = True
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
