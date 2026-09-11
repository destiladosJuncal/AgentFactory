"""
Filtro de salida: enmascara VALORES de secretos en cualquier texto antes de que
llegue al LLM (resultados de herramientas incluidos — ahí está el agujero que
core/marcas.py no cubre: un ejecutar_shell que imprime el .env, o una clave en
el BODY de un login, que el patrón de marcas —session/token/csrf— no atrapa).

Diseño clave: enmascara VALORES, nunca ESTRUCTURA. El nombre de un campo, el
código JS de una página, la forma de un request, un algoritmo de cifrado: todo
eso queda visible, porque es lo que el agente usa para razonar y reversar un
flujo. Solo se tapa el valor concreto de la credencial. Así se cierra la fuga
sin lobotomizar la capacidad del agente de resolver el problema.

No toca la captura, ni la navegación, ni la ejecución: los scripts corren con
los valores REALES; esto solo cambia lo que VE el modelo.
"""

import os
import re

MARCA = "‹secreto›"

# Asignaciones tipo  clave: "abc",  password=abc,  "token": "abc".
# Incluye las palabras en español (clave, contraseña) que faltaban.
_ASSIGN = re.compile(
    r'(?i)("?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|'
    r'secret|client[_-]?secret|password|passwd|pwd|pass|clave|contrase\w*|sesame|'
    r'authorization|x-api-key)"?\s*[:=]\s*"?)([^\s"&,;]{6,})')
# JWT (eyJ........)
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b')
# Bearer <token>
_BEARER = re.compile(r'(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}')


def _valores_env():
    """Valores REALES de secretos del entorno (claves del .env). Coincidencia
    exacta = cero falsos positivos."""
    vals = set()
    for k, v in os.environ.items():
        if v and len(v) >= 8 and re.search(r'(?i)key|token|secret|password|clave|sesame', k):
            vals.add(v)
    return vals


def redactar(texto: str) -> str:
    if not texto:
        return texto
    for v in _valores_env():
        if v in texto:
            texto = texto.replace(v, MARCA)
    texto = _JWT.sub(MARCA, texto)
    texto = _BEARER.sub(lambda m: m.group(1) + MARCA, texto)
    texto = _ASSIGN.sub(lambda m: m.group(1) + MARCA, texto)
    return texto
