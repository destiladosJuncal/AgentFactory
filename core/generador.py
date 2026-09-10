import os
import re
import json
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

from core.herramientas import TOOLS_SCHEMA as TOOLS_PROYECTO
from core.biblioteca import TOOLS_SCHEMA_BIBLIOTECA
from core.utils_tools import resumen_tool, resumen_args
from core.proveedores import crear_proveedor, ErrorProveedor

load_dotenv()

# Topes para que el prompt de reintento no se infle sin control en proyectos
# largos: el código previo y el traceback son lo que más ocupa.
MAX_CODIGO_PROMPT = 4_000
MAX_DIAGNOSTICO_PROMPT = 1_500

NOMBRES_TOOLS_PROYECTO = {t["function"]["name"] for t in TOOLS_PROYECTO}
NOMBRES_TOOLS_BIBLIOTECA = {t["function"]["name"] for t in TOOLS_SCHEMA_BIBLIOTECA}


class Generador:
    def __init__(self, herramientas: Optional[object] = None, biblioteca: Optional[object] = None):
        self.contexto_extra = ""
        self.feedback_acumulado = []

        # `herramientas`: instancia de core.herramientas.Herramientas, confinada
        # a la carpeta del proyecto ACTUAL (leer/escribir/explorar ese proyecto).
        # `biblioteca`: instancia de core.biblioteca.Biblioteca, compartida entre
        # TODOS los proyectos (módulos/herramientas reutilizables).
        self.herramientas = herramientas
        self.biblioteca = biblioteca

        self.tools_habilitadas = os.getenv('AGENTE_TOOLS_ENABLED', '1') != '0'
        self.max_iteraciones_tools = 8

        # El proveedor (DeepSeek o Claude) se elige con AGENTE_PROVEEDOR en el
        # .env. Si no hay credenciales, queda en None = modo simulación.
        self.proveedor = crear_proveedor()

    def generar(self, plan: Dict, historial: List, descripcion: str, feedback: str = "") -> str:
        """Genera código. `feedback` es opcional y retrocompatible."""

        if feedback:
            self.feedback_acumulado.append(feedback)

        if not self.proveedor:
            print("⚠️  Modo simulación")
            return self._codigo_fallback(descripcion)

        prompt = self._construir_prompt(plan, historial, descripcion)

        tools_activas = self.tools_habilitadas and self.herramientas is not None
        biblioteca_activa = self.tools_habilitadas and self.biblioteca is not None


        system_content = (
            "Eres un experto programador Python. Escucha el feedback del usuario "
            "y mejora el código."
        )
        if tools_activas:
            system_content += (
                " Tenés herramientas para explorar y construir el proyecto actual "
                "(buscar archivos, leer archivos, listar directorios, crear carpetas, "
                "escribir archivos auxiliares, y correr comandos de solo lectura)."
            )
        if biblioteca_activa:
            system_content += (
                " Además tenés una BIBLIOTECA COMPARTIDA con módulos/herramientas ya "
                "construidos en proyectos anteriores (wrappers de herramientas CLI, "
                "validadores, clientes HTTP, etc). ANTES de escribir algo desde cero "
                "que podría ya existir, llamá a listar_biblioteca. Si encontrás algo "
                "útil, leelo con leer_modulo_biblioteca y reutilizalo/adaptalo en vez "
                "de reimplementarlo. Si vos mismo construís algo genérico y reutilizable "
                "(no específico de este proyecto puntual), publicalo con "
                "publicar_modulo_biblioteca para que esté disponible en el futuro."
            )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ]

        tools = []
        if tools_activas:
            tools += TOOLS_PROYECTO
        if biblioteca_activa:
            tools += TOOLS_SCHEMA_BIBLIOTECA
        tools = tools or None

        try:
            for _ in range(self.max_iteraciones_tools):
                respuesta = self.proveedor.completar(
                    mensajes=messages,
                    tools=tools,
                    temperature=0.3,
                )

                tool_calls = respuesta.tool_calls
                if tool_calls:
                    messages.append({
                        "role": "assistant",
                        "content": respuesta.texto,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            } for tc in tool_calls
                        ]
                    })

                    for tc in tool_calls:
                        raw_args = tc.function.arguments or ""
                        try:
                            args = json.loads(raw_args or "{}")
                        except json.JSONDecodeError:
                            resultado = {
                                "error": (
                                    f"Los argumentos de esta llamada llegaron incompletos "
                                    f"({len(raw_args)} caracteres, el JSON no cierra) — tu "
                                    f"respuesta se cortó por longitud, no es un error de la "
                                    f"herramienta. Repetí la llamada, si es escribir_archivo "
                                    f"dividí el contenido en varias escrituras más chicas."
                                )
                            }
                            print(f"   🔧 {tc.function.name}(<JSON incompleto, {len(raw_args)} chars>) -> {resumen_tool(resultado)}")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(resultado, ensure_ascii=False)[:4000]
                            })
                            continue

                        resultado = self._despachar_tool(tc.function.name, args)
                        print(f"   🔧 {tc.function.name}({resumen_args(args)}) -> {resumen_tool(resultado)}")

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(resultado, ensure_ascii=False)[:4000]
                        })

                    continue

                return self._extraer_codigo(respuesta.texto)

            print("⚠️  Se agotaron las iteraciones de herramientas, usando fallback")
            return self._codigo_fallback(descripcion)

        except ErrorProveedor as e:
            print(f"⚠️ Error con {self.proveedor.nombre}: {e}")
            return self._codigo_fallback(descripcion)
        except Exception as e:
            print(f"⚠️ Error inesperado generando código: {e}")
            return self._codigo_fallback(descripcion)

    def _despachar_tool(self, nombre: str, args: dict) -> Dict[str, Any]:
        """Rutea la tool call al sandbox del proyecto o a la biblioteca compartida
        según a qué catálogo pertenece el nombre de la herramienta."""
        if nombre in NOMBRES_TOOLS_PROYECTO:
            if not self.herramientas:
                return {"error": "Herramientas de proyecto no disponibles"}
            return self.herramientas.ejecutar_tool(nombre, args)
        if nombre in NOMBRES_TOOLS_BIBLIOTECA:
            if not self.biblioteca:
                return {"error": "Biblioteca compartida no disponible"}
            return self.biblioteca.ejecutar_tool(nombre, args)
        return {"error": f"Herramienta desconocida: {nombre}"}

    def _construir_prompt(self, plan, historial, descripcion) -> str:
        prompt = f"""
# OBJETIVO
{descripcion}

# ESTRATEGIA ACTUAL
{plan.get('estrategia', 'Implementación directa')}

# ENFOQUE
{plan.get('enfoque', 'Código eficiente y legible')}
"""

        if plan.get('feedback_usuario'):
            prompt += f"\n# FEEDBACK DEL USUARIO (prioritario)\n{plan.get('feedback_usuario')}\n"

        if self.feedback_acumulado:
            prompt += "\n# HISTORIAL DE FEEDBACK DEL USUARIO\n"
            for i, fb in enumerate(self.feedback_acumulado[-3:], 1):
                prompt += f"{i}. {fb}\n"

        if historial:
            # De las iteraciones viejas alcanza con el resumen; de la ÚLTIMA va
            # también el código, porque sin verlo el modelo reescribe desde cero
            # cada vuelta en vez de corregir (y suele repetir el mismo error).
            previas = historial[-3:]
            prompt += "\n# ITERACIONES PREVIAS\n"
            for idx, h in enumerate(previas[:-1], len(historial) - len(previas) + 1):
                prompt += (f"\nIteración {idx} — puntaje "
                           f"{h.get('metricas', {}).get('puntaje_global', 0):.0%}: "
                           f"{(h.get('diagnostico') or 'sin diagnóstico').splitlines()[0][:120]}\n")

            ultima = previas[-1]
            n = len(historial)
            prompt += (f"\n## Última iteración (la {n}) — puntaje "
                       f"{ultima.get('metricas', {}).get('puntaje_global', 0):.0%}\n")

            diagnostico = ultima.get('diagnostico') or 'sin diagnóstico'
            prompt += f"\n### Qué pasó al ejecutarla\n{diagnostico[:MAX_DIAGNOSTICO_PROMPT]}\n"

            salida = (ultima.get('resultado') or {}).get('stdout', '').strip()
            if salida:
                prompt += f"\n### Lo que imprimió\n{salida[:600]}\n"

            codigo_previo = ultima.get('codigo', '')
            if codigo_previo:
                recorte = codigo_previo[:MAX_CODIGO_PROMPT]
                if len(codigo_previo) > MAX_CODIGO_PROMPT:
                    recorte += "\n# … (recortado)"
                prompt += (f"\n### El código que escribiste (corregilo, no lo "
                           f"reescribas desde cero si el problema es puntual)\n"
                           f"```python\n{recorte}\n```\n")

        if self.contexto_extra:
            prompt += f"\n# FEEDBACK AUTOMÁTICO\n{self.contexto_extra}\n"

        prompt += """
# REQUERIMIENTOS
- Si hay una iteración previa que falló, PARTÍ DE ESE CÓDIGO y corregí lo que indica el diagnóstico. No empieces de cero salvo que el enfoque esté equivocado de raíz.
- Si algo parecido a lo que necesitás ya podría existir de un proyecto anterior, revisá la biblioteca compartida antes de escribirlo desde cero.
- Si necesitás contexto de ESTE proyecto (código previo, archivos generados), usá las herramientas de proyecto.
- Cuando tengas el código final, respondé SOLO con el código Python
- Incluye docstring
- Usa nombres descriptivos
- Maneja casos borde

Respondé con el código dentro de un bloque ```python ... ```
"""
        return prompt

    def actualizar_contexto(self, diagnostico: str):
        self.contexto_extra += f"\nCorrección necesaria: {diagnostico}\n"

    def _extraer_codigo(self, response: str) -> str:
        patron = r'```python\s*(.*?)\s*```'
        match = re.search(patron, response, re.DOTALL)
        if match:
            return match.group(1).strip()

        patron2 = r'```\s*(.*?)\s*```'
        match = re.search(patron2, response, re.DOTALL)
        if match:
            return match.group(1).strip()

        return response.strip()

    def _codigo_fallback(self, descripcion) -> str:
        return '''def solucion():
    """
    TODO: Implementar según el objetivo (modo simulación, sin API key configurada).
    """
    pass
'''
