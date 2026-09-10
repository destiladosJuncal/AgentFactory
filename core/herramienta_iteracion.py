"""
Herramienta 'iterar_codigo': le da al chat la posibilidad de disparar el modo
iterativo con objetivo + puntaje (el mismo motor que ya usa
core.agente_interactivo.AgenteInteractivo) como una tool call más, en vez de
tener que salir a otro programa aparte.

Corre en modo NO interactivo (no puede pausar a mitad de una tool call para
preguntarle algo a la persona), pero crea/retoma un proyecto persistente
normal en $HOME/tmp/agent_code/<proyecto>/ — después se puede seguir
iterando desde ahí con main_interactivo.py como cualquier otro proyecto,
o volver a invocar esta misma tool desde el chat.

Importante: esta tool NO se ofrece dentro del propio Generador del modo
iterativo (core.generador.Generador solo conoce TOOLS_PROYECTO +
TOOLS_SCHEMA_BIBLIOTECA). Eso es deliberado: evita que una corrida iterativa
pueda disparar recursivamente otra corrida iterativa desde adentro.
"""

from pathlib import Path
from typing import Dict, Any, List

from core.proyectos import GestorProyectos
from core.agente_interactivo import AgenteInteractivo, OBJETIVOS_DEFAULT

MAX_ITERACIONES_TOOL = 10  # tope defensivo para una corrida disparada desde el chat


def iterar_codigo(descripcion: str = "", umbral_global: float = 0.75,
                   max_iteraciones: int = 8, nombre_proyecto: str = "") -> Dict[str, Any]:
    if not descripcion and not nombre_proyecto:
        return {"error": "Falta 'descripcion' del objetivo a resolver (o 'nombre_proyecto' para retomar uno existente)"}

    try:
        max_iteraciones = max(1, min(int(max_iteraciones), MAX_ITERACIONES_TOOL))
    except (TypeError, ValueError):
        max_iteraciones = 8
    try:
        umbral_global = max(0.0, min(float(umbral_global), 1.0))
    except (TypeError, ValueError):
        umbral_global = 0.75

    gestor = GestorProyectos()

    proyecto_dir = None
    if nombre_proyecto:
        proyecto_dir = gestor.cargar_proyecto(nombre_proyecto)
        if proyecto_dir is None:
            return {"error": f"No existe un proyecto llamado '{nombre_proyecto}'"}

    if proyecto_dir is None:
        proyecto_dir = gestor.crear_proyecto(
            descripcion=descripcion,
            objetivos=OBJETIVOS_DEFAULT,
            umbral_global=umbral_global,
            max_iteraciones=max_iteraciones
        )

    agente = AgenteInteractivo(proyecto_dir)
    agente.interaccion_activa = False  # no puede pausar a preguntar desde una tool call

    # Si estamos retomando un proyecto que ya había llegado a su límite,
    # extendemos con las iteraciones pedidas en esta invocación.
    if not agente.objetivos_alcanzados and agente.iteracion >= agente.max_iteraciones:
        agente.max_iteraciones += max_iteraciones

    resultado = agente.ejecutar()
    agente.limpiar()

    ruta_solucion = Path(resultado['proyecto_dir']) / ("solucion.py" if resultado['exito'] else "ultima_version.py")

    return {
        "proyecto": Path(resultado['proyecto_dir']).name,
        "exito": resultado['exito'],
        "iteraciones": resultado['iteraciones'],
        "mejor_puntaje": round(resultado['mejor_puntaje'], 3),
        "codigo_final": resultado['codigo_final'] if resultado['exito'] else None,
        "ruta_solucion": str(ruta_solucion) if ruta_solucion.exists() else None
    }


TOOLS_SCHEMA_ITERACION: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "iterar_codigo",
            "description": (
                "Dispara el modo iterativo del agente: genera código, lo ejecuta, "
                "lo evalúa con un puntaje (funcionalidad/eficiencia/calidad), y "
                "repite automáticamente hasta cumplir un umbral o agotar el límite "
                "de iteraciones. Usala cuando la tarea conviene resolverla con "
                "feedback automático de ejecución/tests en vez de una sola "
                "respuesta conversacional — por ejemplo, algo con casos borde no "
                "triviales que valga la pena probar y corregir varias veces. "
                "Crea (o retoma) un proyecto persistente separado del workspace "
                "de este chat; podés volver a llamar a esta tool más adelante "
                "pasándole el mismo nombre_proyecto para seguir iterando."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion": {"type": "string", "description": "Qué tiene que construir/resolver el código (requerido si es un proyecto nuevo)"},
                    "umbral_global": {"type": "number", "description": "Puntaje mínimo (0-1) para considerarlo resuelto. Default 0.75"},
                    "max_iteraciones": {"type": "integer", "description": f"Máximo de iteraciones en ESTA corrida (tope {MAX_ITERACIONES_TOOL})"},
                    "nombre_proyecto": {"type": "string", "description": "Nombre exacto de un proyecto existente, si querés retomarlo en vez de crear uno nuevo"}
                },
                "required": []
            }
        }
    }
]
