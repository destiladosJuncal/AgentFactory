"""Helpers compartidos entre Generador (modo iterativo) y Chat (modo conversacional)."""

from typing import Dict, Any

MAX_ARG_EN_LOG = 120


def resumen_args(args: dict) -> str:
    """Argumentos de una tool call, recortados para el log.

    Sin esto, un `escribir_archivo` con un script entero adentro escupe miles
    de caracteres a la consola y tapa todo lo demás."""
    if not isinstance(args, dict):
        return str(args)[:MAX_ARG_EN_LOG]
    partes = []
    for clave, valor in args.items():
        texto = valor if isinstance(valor, str) else repr(valor)
        if len(texto) > MAX_ARG_EN_LOG:
            texto = texto[:MAX_ARG_EN_LOG].replace("\n", "\\n") + f"… (+{len(texto)-MAX_ARG_EN_LOG} chars)"
        partes.append(f"{clave}={texto!r}" if isinstance(valor, str) else f"{clave}={texto}")
    return "{" + ", ".join(partes) + "}"


def resumen_tool(resultado: Dict[str, Any]) -> str:
    """Resumen corto de un resultado de tool call, solo para logging en consola."""
    if "error" in resultado:
        return f"error: {resultado['error']}"
    if "encontrados" in resultado:
        return f"{resultado['total']} archivo(s)"
    if "entradas" in resultado:
        return f"{len(resultado['entradas'])} entrada(s)"
    if "modulos" in resultado:
        return f"{resultado['total']} módulo(s) en biblioteca"
    if "contenido" in resultado and "nombre" in resultado:
        return f"módulo '{resultado['nombre']}' ({len(resultado['contenido'])} chars)"
    if "contenido" in resultado:
        return f"{resultado.get('longitud_total', len(resultado['contenido']))} chars"
    if "escrito" in resultado:
        return f"escrito {resultado['escrito']} ({resultado['bytes']} bytes)"
    if "creada" in resultado:
        return f"carpeta creada: {resultado['creada']}"
    if "publicado" in resultado:
        return f"publicado en biblioteca: {resultado['publicado']}"
    if "actualizado" in resultado:
        return f"actualizado en biblioteca: {resultado['actualizado']}"
    if "preguntas" in resultado:
        n = resultado.get("total", len(resultado["preguntas"]))
        if resultado.get("respuestas"):
            fallidas = sum(1 for r in resultado["respuestas"] if "error" in r)
            extra = f", {fallidas} fallida(s)" if fallidas else ""
            return (f"{n} sub-pregunta(s), {len(resultado['respuestas'])} respondida(s) "
                    f"en paralelo [{resultado.get('modo', '')}]{extra}")
        return f"{n} sub-pregunta(s)"
    if "modulo" in resultado and "funcion" in resultado:
        return f"ejecutado {resultado['modulo']}.{resultado['funcion']}()"
    if "borrado" in resultado:
        return f"borrado de la biblioteca: {resultado['borrado']}"
    if "salida" in resultado:
        # Un comando que devolvió != 0 falló, aunque haya escrito algo. Decir
        # 'ok' acá hacía que el modelo creyera que iba bien y siguiera a ciegas.
        codigo = resultado.get("codigo_retorno")
        if codigo:
            primera = (resultado["salida"] or "").strip().splitlines()
            detalle = f": {primera[0][:70]}" if primera else ""
            return f"falló (código {codigo}){detalle}"
        return "ok"
    if "exito" in resultado and "iteraciones" in resultado:
        estado = "✅ resuelto" if resultado["exito"] else "⏸️ sin llegar al umbral"
        return f"{estado} en {resultado['iteraciones']} iteración(es), puntaje {resultado['mejor_puntaje']:.0%}"
    return "ok"
