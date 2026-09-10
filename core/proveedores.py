"""
Capa de proveedores de LLM: permite pedirle al agente que use DeepSeek o Claude
sin cambiar nada del resto del código.

El resto del proyecto (core/generador.py, core/chat.py) habla un solo dialecto:
mensajes y herramientas en formato OpenAI/DeepSeek (`role`/`content`/`tool_calls`,
tools con `{"type": "function", "function": {...}}`). Eso es también lo que se
persiste en disco (mensajes.json), así que se puede cambiar de proveedor a mitad
de una conversación sin romper el historial.

Cada proveedor recibe ese dialecto y devuelve una `Respuesta` con la misma forma:

    respuesta.texto        -> str
    respuesta.tool_calls   -> lista de objetos con .id / .function.name /
                              .function.arguments  (igual que el SDK de OpenAI)

- DeepSeek habla ese dialecto de forma nativa (SDK `openai` apuntado a su API).
- Claude NO: usa la Messages API de Anthropic, con bloques de contenido
  (`tool_use` / `tool_result`) en vez de `tool_calls`. `ProveedorClaude` hace la
  traducción en ambos sentidos.

Configuración (.env):

    AGENTE_PROVEEDOR=claude        # o 'deepseek' (default)
    ANTHROPIC_API_KEY=sk-ant-...
    ANTHROPIC_MODEL=claude-opus-4-8    # opcional
    ANTHROPIC_EFFORT=high              # low|medium|high|xhigh|max, opcional
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# Modelo por defecto: el más capaz de la familia Opus.
MODELO_CLAUDE_DEFAULT = "claude-opus-4-8"
MODELO_DEEPSEEK_DEFAULT = "deepseek-chat"
MODELO_QWEN_DEFAULT = "qwen-plus"
MODELO_GEMINI_DEFAULT = "gemini-3.5-flash"
# Endpoint compatible con OpenAI de la Gemini API (auth por API key). Para
# Vertex AI / Google Enterprise el endpoint y la auth son otros (ver GEMINI_API_URL).
URL_GEMINI_DEFAULT = "https://generativelanguage.googleapis.com/v1beta/openai/"
# DashScope internacional (fuera de China). Para China: dashscope.aliyuncs.com
URL_QWEN_DEFAULT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Sin streaming, este es el techo razonable para no chocar con el timeout HTTP
# del SDK. Con pensamiento adaptativo, el razonamiento también cuenta acá.
MAX_TOKENS_DEFAULT = 16000


# --- Forma común de la respuesta (imita al SDK de OpenAI a propósito) --------

@dataclass
class _Funcion:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    function: _Funcion
    type: str = "function"


@dataclass
class Uso:
    """Tokens de una llamada. `costo()` devuelve None si no hay precio cargado
    para ese modelo — ver PRECIOS más abajo."""
    entrada: int = 0
    salida: int = 0
    modelo: str = ""

    @property
    def total(self) -> int:
        return self.entrada + self.salida

    def costo(self) -> Optional[float]:
        precio = precio_de(self.modelo)
        if precio is None:
            return None
        entrada_mtok, salida_mtok = precio
        return (self.entrada / 1_000_000) * entrada_mtok + \
               (self.salida / 1_000_000) * salida_mtok


# Precios por millón de tokens (entrada, salida). Se cargan del .env con la
# forma PRECIO_<MODELO>_IN / _OUT, donde <MODELO> es el id en mayúsculas y con
# guiones bajos. Ejemplo:
#
#     PRECIO_CLAUDE_OPUS_4_8_IN=5
#     PRECIO_CLAUDE_OPUS_4_8_OUT=25
#
# Vienen VACÍOS a propósito: no quiero inventar precios y mostrarte plata mal
# calculada. Mientras no los cargues, la UI muestra tokens y segundos, y el
# costo aparece como '—'. Los valores reales están en la página de precios de
# cada proveedor.
def precio_de(modelo: str) -> Optional[Tuple[float, float]]:
    if not modelo:
        return None
    clave = re.sub(r"[^A-Z0-9]+", "_", modelo.upper()).strip("_")
    entrada = os.getenv(f"PRECIO_{clave}_IN")
    salida = os.getenv(f"PRECIO_{clave}_OUT")
    if not entrada or not salida:
        return None
    try:
        return float(entrada), float(salida)
    except ValueError:
        return None


@dataclass
class Respuesta:
    texto: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    uso: Uso = field(default_factory=Uso)
    # Motivo por el que el modelo cortó, si el proveedor lo informa
    # ('refusal', 'max_tokens', ...). Sirve para no leer contenido vacío.
    motivo_fin: Optional[str] = None


class ErrorProveedor(RuntimeError):
    """Falla al hablar con el proveedor (red, credenciales, request inválida)."""


# --- DeepSeek (y cualquier API compatible con OpenAI) -----------------------

class ProveedorDeepSeek:
    nombre = "deepseek"

    def __init__(self):
        from openai import OpenAI  # import perezoso: solo si se usa

        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.api_url = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
        self.modelo = os.getenv("DEEPSEEK_MODEL", MODELO_DEEPSEEK_DEFAULT)

        # La API de DeepSeek solo acepta los ids en minúscula y devuelve un 400
        # si les cambiás una mayúscula ('DeepSeek-V4-Pro' vs 'deepseek-v4-pro').
        # Normalizamos, pero SOLO contra su propio endpoint: si apuntás esta
        # misma clase a otro proveedor compatible (Ollama, un gateway), los
        # nombres pueden ser sensibles a mayúsculas y no hay que tocarlos.
        if "api.deepseek.com" in self.api_url and self.modelo != self.modelo.lower():
            print(f"ℹ️  Normalizo el modelo '{self.modelo}' -> '{self.modelo.lower()}' "
                  f"(la API de DeepSeek los quiere en minúscula)")
            self.modelo = self.modelo.lower()

        if not self.api_key:
            raise ErrorProveedor("Falta DEEPSEEK_API_KEY")
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_url)

    def descripcion(self) -> str:
        return f"DeepSeek · {self.modelo}"

    def completar(self, mensajes: List[Dict[str, Any]],
                  tools: Optional[List[Dict[str, Any]]] = None,
                  temperature: float = 0.3,
                  max_tokens: int = MAX_TOKENS_DEFAULT,
                  al_fragmento: Optional[Callable[[str], None]] = None,
                  cancelado: Optional[Callable[[], bool]] = None) -> Respuesta:
        if al_fragmento is not None:
            return self._completar_stream(mensajes, tools, temperature,
                                          al_fragmento, cancelado)

        kwargs: Dict[str, Any] = dict(
            model=self.modelo,
            messages=mensajes,
            temperature=temperature,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            respuesta = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            raise ErrorProveedor(str(e)) from e

        msg = respuesta.choices[0].message
        llamadas = [
            ToolCall(id=tc.id, function=_Funcion(name=tc.function.name,
                                                 arguments=tc.function.arguments or ""))
            for tc in (getattr(msg, "tool_calls", None) or [])
        ]
        u = getattr(respuesta, "usage", None)
        return Respuesta(
            texto=msg.content or "",
            tool_calls=llamadas,
            motivo_fin=getattr(respuesta.choices[0], "finish_reason", None),
            uso=Uso(entrada=getattr(u, "prompt_tokens", 0) or 0,
                    salida=getattr(u, "completion_tokens", 0) or 0,
                    modelo=self.modelo),
        )


    def _completar_stream(self, mensajes, tools, temperature, al_fragmento,
                          cancelado=None) -> Respuesta:
        """Igual que completar(), pero va entregando el texto a medida que llega.

        Las tool calls también vienen en pedazos: el `id` y el nombre llegan una
        vez y los argumentos se van armando trozo a trozo, así que hay que
        acumularlos por índice antes de poder usarlos."""
        kwargs: Dict[str, Any] = dict(
            model=self.modelo, messages=mensajes, temperature=temperature,
            stream=True,
            # Sin esto el stream no informa tokens y las métricas quedarían en 0.
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        texto: List[str] = []
        parciales: Dict[int, Dict[str, str]] = {}
        uso = Uso(modelo=self.modelo)
        motivo = None

        flujo = None
        try:
            flujo = self.client.chat.completions.create(**kwargs)
            for chunk in flujo:
                # Detener: cortamos la generación en el acto (cerramos el stream,
                # que aborta la request), no al terminar el mensaje entero.
                if cancelado is not None and cancelado():
                    break
                if getattr(chunk, "usage", None):
                    uso.entrada = chunk.usage.prompt_tokens or 0
                    uso.salida = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                eleccion = chunk.choices[0]
                motivo = eleccion.finish_reason or motivo
                delta = eleccion.delta

                if getattr(delta, "content", None):
                    texto.append(delta.content)
                    al_fragmento(delta.content)

                for tc in (getattr(delta, "tool_calls", None) or []):
                    acumulado = parciales.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        acumulado["id"] = tc.id
                    if tc.function and tc.function.name:
                        acumulado["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        acumulado["arguments"] += tc.function.arguments
        except Exception as e:
            raise ErrorProveedor(str(e)) from e
        finally:
            # Cerrar el stream libera la conexión aunque hayamos cortado por
            # cancelación (el SDK no sabe que dejamos de iterar).
            if flujo is not None:
                try:
                    flujo.close()
                except Exception:
                    pass

        llamadas = [
            ToolCall(id=a["id"], function=_Funcion(name=a["name"], arguments=a["arguments"]))
            for _, a in sorted(parciales.items()) if a["name"]
        ]
        return Respuesta(texto="".join(texto), tool_calls=llamadas,
                         motivo_fin=motivo, uso=uso)


# --- Qwen (Alibaba DashScope, API compatible con OpenAI) --------------------

class ProveedorQwen(ProveedorDeepSeek):
    """Qwen expone una API compatible con OpenAI, igual que DeepSeek, así que
    reutiliza toda la lógica de request/stream/tool-calls de ProveedorDeepSeek y
    solo cambia credenciales, endpoint y nombre. Los ids de Qwen SON sensibles a
    mayúsculas (qwen-max, qwen3-max), por eso no heredamos la normalización a
    minúscula de DeepSeek: sobreescribimos __init__ entero."""

    nombre = "qwen"

    def __init__(self):
        from openai import OpenAI
        self.api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        self.api_url = os.getenv("QWEN_API_URL", URL_QWEN_DEFAULT)
        self.modelo = os.getenv("QWEN_MODEL", MODELO_QWEN_DEFAULT)
        if not self.api_key:
            raise ErrorProveedor("Falta QWEN_API_KEY")
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_url)

    def descripcion(self) -> str:
        return f"Qwen · {self.modelo}"


# --- Gemini (Google, endpoint compatible con OpenAI, auth por API key) ------

class ProveedorGemini(ProveedorDeepSeek):
    """Gemini expone un endpoint compatible con OpenAI, así que reutiliza toda la
    lógica de request/stream/tool-calls de ProveedorDeepSeek y solo cambia
    credenciales, endpoint y nombre. Los ids de Gemini son sensibles a
    mayúsculas (gemini-2.5-flash, gemini-2.5-pro): por eso sobreescribimos
    __init__ entero y no heredamos la normalización a minúscula de DeepSeek.

    Auth por API key (Google AI Studio / clave de proyecto GCP). Para Vertex AI
    el endpoint y la auth son distintos (OAuth + proyecto/región): en ese caso
    se apunta GEMINI_API_URL a un endpoint compatible y se usa la clave que
    corresponda."""

    nombre = "gemini"

    def __init__(self):
        from openai import OpenAI
        self.api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                        or os.getenv("GOOGLE_GENAI_API_KEY"))
        self.api_url = os.getenv("GEMINI_API_URL", URL_GEMINI_DEFAULT)
        self.modelo = os.getenv("GEMINI_MODEL", MODELO_GEMINI_DEFAULT)
        if not self.api_key:
            raise ErrorProveedor("Falta GEMINI_API_KEY")
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_url)

    def descripcion(self) -> str:
        return f"Gemini · {self.modelo}"


# --- Claude (Anthropic Messages API) ---------------------------------------

class ProveedorClaude:
    """Traduce el dialecto OpenAI del proyecto a la Messages API de Anthropic.

    Detalle importante sobre el pensamiento extendido: cuando el modelo piensa y
    además pide herramientas, los bloques `thinking` de ese turno tienen que
    volver TAL CUAL en la siguiente request del mismo turno (la API valida su
    firma). Como el historial que maneja el proyecto es formato OpenAI y no tiene
    dónde guardarlos, los cacheamos acá en memoria, indexados por el id de la
    primera tool call de ese turno, y los reinyectamos al reconstruir el mensaje.

    Entre turnos distintos (o al retomar una conversación de disco) el caché no
    los tiene: ahí se reconstruye el mensaje sin bloques de pensamiento, que es
    válido — la regla es no MODIFICAR los que se mandan, no mandarlos siempre.
    """

    nombre = "claude"

    def __init__(self):
        try:
            import anthropic  # import perezoso
        except ImportError as e:
            raise ErrorProveedor(
                "Falta el SDK de Anthropic. Instalalo con:  pip install anthropic"
            ) from e

        self._anthropic = anthropic
        self.modelo = os.getenv("ANTHROPIC_MODEL", MODELO_CLAUDE_DEFAULT)
        self.effort = os.getenv("ANTHROPIC_EFFORT", "high")

        # El SDK resuelve credenciales solo (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
        # o un perfil de `ant auth login`), así que no forzamos api_key=...
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise ErrorProveedor(
                "Falta ANTHROPIC_API_KEY (ponela en el .env de la instalación)"
            )
        self.client = anthropic.Anthropic()

        # {id_de_la_primera_tool_call: [bloques de contenido nativos]}
        self._bloques_nativos: Dict[str, List[Any]] = {}

    def descripcion(self) -> str:
        return f"Claude · {self.modelo}"

    # -- traducción: proyecto -> Anthropic --------------------------------

    @staticmethod
    def _traducir_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        traducidas = []
        for t in tools:
            fn = t.get("function", t)
            traducidas.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return traducidas

    def _traducir_mensajes(self, mensajes: List[Dict[str, Any]]):
        """Devuelve (system, messages) en formato Anthropic.

        - Los mensajes con role 'system' se juntan en el parámetro `system`.
        - Los resultados de herramientas consecutivos se agrupan en UN solo
          mensaje de usuario, que es lo que exige la API.
        """
        partes_system: List[str] = []
        salida: List[Dict[str, Any]] = []

        def _agregar(rol: str, bloques: List[Any]):
            if not bloques:
                return
            # Resultados de tools consecutivos van todos en el mismo turno.
            if salida and salida[-1]["role"] == rol == "user":
                previo = salida[-1]["content"]
                es_tool = all(isinstance(b, dict) and b.get("type") == "tool_result"
                              for b in bloques)
                previo_tool = all(isinstance(b, dict) and b.get("type") == "tool_result"
                                  for b in previo)
                if es_tool and previo_tool:
                    previo.extend(bloques)
                    return
            salida.append({"role": rol, "content": bloques})

        for m in mensajes:
            rol = m.get("role")
            contenido = m.get("content") or ""

            if rol == "system":
                if contenido:
                    partes_system.append(contenido)

            elif rol == "user":
                if contenido:
                    _agregar("user", [{"type": "text", "text": contenido}])

            elif rol == "tool":
                _agregar("user", [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": contenido or "(sin salida)",
                }])

            elif rol == "assistant":
                llamadas = m.get("tool_calls") or []
                clave = llamadas[0]["id"] if llamadas else None
                nativos = self._bloques_nativos.get(clave) if clave else None

                if nativos is not None:
                    # Turno de este mismo ciclo: devolvemos los bloques
                    # originales (incluye `thinking` con su firma intacta).
                    salida.append({"role": "assistant", "content": nativos})
                    continue

                bloques: List[Any] = []
                if contenido:
                    bloques.append({"type": "text", "text": contenido})
                for tc in llamadas:
                    try:
                        entrada = json.loads(tc["function"].get("arguments") or "{}")
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        entrada = {}
                    bloques.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": entrada,
                    })
                _agregar("assistant", bloques)

        # La API exige que el primer mensaje sea del usuario.
        while salida and salida[0]["role"] != "user":
            salida.pop(0)

        return "\n\n".join(partes_system), salida

    # -- llamada ----------------------------------------------------------

    def completar(self, mensajes: List[Dict[str, Any]],
                  tools: Optional[List[Dict[str, Any]]] = None,
                  temperature: float = 0.3,
                  max_tokens: int = MAX_TOKENS_DEFAULT,
                  al_fragmento: Optional[Callable[[str], None]] = None,
                  cancelado: Optional[Callable[[], bool]] = None) -> Respuesta:
        # `temperature` se ignora a propósito: Claude Opus 4.7+ rechaza los
        # parámetros de sampling con un 400. El estilo se guía por el prompt.
        system, mensajes_nativos = self._traducir_mensajes(mensajes)
        if not mensajes_nativos:
            raise ErrorProveedor("No hay ningún mensaje de usuario para enviar")

        kwargs: Dict[str, Any] = dict(
            model=self.modelo,
            max_tokens=max_tokens,
            messages=mensajes_nativos,
            # En Opus 4.8 el pensamiento NO viene activado por omisión: hay que
            # pedirlo explícitamente. Adaptativo = el modelo decide cuánto pensar.
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._traducir_tools(tools)

        try:
            if al_fragmento is not None:
                # El SDK de Anthropic arma el mensaje final por su cuenta
                # mientras vamos consumiendo el texto: nos ahorra reensamblar
                # los bloques (incluidos los de pensamiento y sus firmas).
                with self.client.messages.stream(**kwargs) as flujo:
                    for fragmento in flujo.text_stream:
                        # Detener: al salir del `with` se cierra el stream y se
                        # aborta la request, sin esperar a que termine.
                        if cancelado is not None and cancelado():
                            break
                        al_fragmento(fragmento)
                    else:
                        respuesta = flujo.get_final_message()
                if cancelado is not None and cancelado():
                    return Respuesta(texto="", motivo_fin="cancelado")
            else:
                respuesta = self.client.messages.create(**kwargs)
        except self._anthropic.APIStatusError as e:
            raise ErrorProveedor(f"{e.status_code} · {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise ErrorProveedor(f"No pude conectarme a la API de Anthropic: {e}") from e
        except Exception as e:
            raise ErrorProveedor(str(e)) from e

        if respuesta.stop_reason == "refusal":
            detalle = getattr(respuesta, "stop_details", None)
            motivo = getattr(detalle, "explanation", None) or "sin detalle"
            return Respuesta(
                texto=f"⚠️ Claude declinó responder este pedido ({motivo}).",
                motivo_fin="refusal",
            )

        textos: List[str] = []
        llamadas: List[ToolCall] = []
        for bloque in respuesta.content:
            if bloque.type == "text":
                textos.append(bloque.text)
            elif bloque.type == "tool_use":
                llamadas.append(ToolCall(
                    id=bloque.id,
                    function=_Funcion(name=bloque.name,
                                      arguments=json.dumps(bloque.input, ensure_ascii=False)),
                ))

        if llamadas:
            # Guardamos los bloques crudos para poder devolverlos intactos en la
            # próxima vuelta del ciclo de herramientas (firma del `thinking`).
            self._bloques_nativos[llamadas[0].id] = respuesta.content
            # No dejamos que el caché crezca sin techo en conversaciones largas.
            if len(self._bloques_nativos) > 40:
                for clave in list(self._bloques_nativos)[:-20]:
                    self._bloques_nativos.pop(clave, None)

        u = getattr(respuesta, "usage", None)
        return Respuesta(
            texto="\n".join(t for t in textos if t),
            tool_calls=llamadas,
            motivo_fin=respuesta.stop_reason,
            uso=Uso(entrada=getattr(u, "input_tokens", 0) or 0,
                    salida=getattr(u, "output_tokens", 0) or 0,
                    modelo=self.modelo),
        )


# --- Fábrica ----------------------------------------------------------------

# Catálogo para el selector de la UI: (etiqueta, proveedor, id del modelo).
# 'combinado' no es un modelo: le dice a la conversación que pregunte, por
# mensaje, con cuál resolverlo.
MODELOS_DISPONIBLES: List[Tuple[str, str, str]] = [
    ("DeepSeek · v4-pro (más capaz)",   "deepseek", "deepseek-v4-pro"),
    ("DeepSeek · v4-flash (más rápido)", "deepseek", "deepseek-v4-flash"),
    ("Claude · Opus 4.8 (más capaz)",   "claude",   "claude-opus-4-8"),
    ("Claude · Sonnet 5 (equilibrado)", "claude",   "claude-sonnet-5"),
    ("Claude · Haiku 4.5 (más rápido)", "claude",   "claude-haiku-4-5-20251001"),
    ("Qwen · Max (más capaz)",          "qwen",     "qwen-max"),
    ("Qwen · Plus (equilibrado)",       "qwen",     "qwen-plus"),
    ("Qwen · Turbo (más rápido)",       "qwen",     "qwen-turbo"),
    ("Gemini · 3.7 Flash (más rápido)", "gemini",   "gemini-3.7-flash"),
    ("Gemini · 3.5 Flash (equilibrado)", "gemini",  "gemini-3.5-flash"),
    ("Gemini · 3.1 Pro (más capaz)",    "gemini",   "gemini-3.1-pro-preview"),
    ("Combinado (preguntar en cada mensaje)", "combinado", ""),
]


def etiqueta_de(proveedor: str, modelo: str) -> str:
    for etiqueta, p, m in MODELOS_DISPONIBLES:
        if p == proveedor and m == modelo:
            return etiqueta
    return f"{proveedor} · {modelo}" if modelo else (proveedor or "por defecto")


def desde_etiqueta(etiqueta: str) -> Tuple[str, str]:
    for eti, p, m in MODELOS_DISPONIBLES:
        if eti == etiqueta:
            return p, m
    return "", ""


def proveedor_configurado() -> str:
    """Nombre del proveedor pedido en el entorno ('claude' o 'deepseek')."""
    return (os.getenv("AGENTE_PROVEEDOR") or "deepseek").strip().lower()


def crear_proveedor(nombre: Optional[str] = None):
    """Devuelve el proveedor pedido, o None si no hay credenciales (modo simulación).

    Nunca levanta: si falla, avisa por consola y devuelve None, que es lo que el
    resto del código ya interpreta como 'no hay API configurada'.
    """
    nombre = (nombre or proveedor_configurado()).lower()
    clases = {"claude": ProveedorClaude, "anthropic": ProveedorClaude,
              "deepseek": ProveedorDeepSeek, "qwen": ProveedorQwen,
              "gemini": ProveedorGemini, "google": ProveedorGemini}

    clase = clases.get(nombre)
    if clase is None:
        print(f"⚠️  Proveedor desconocido: '{nombre}'. Usá 'claude', 'deepseek' o 'qwen'.")
        return None

    try:
        proveedor = clase()
    except ErrorProveedor as e:
        print(f"⚠️  Modo simulación — {nombre}: {e}")
        return None

    print(f"✅ {proveedor.descripcion()}")
    return proveedor
