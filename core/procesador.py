"""
Preprocesador de preguntas largas: descompone un texto en sub-preguntas
atómicas y las manda a la IA EN PARALELO.

Para qué sirve: un desarrollador escribe un párrafo con cinco preguntas
enredadas. Contestarlo de una sola pasada da una respuesta larga y despareja.
Partirlo en preguntas independientes y resolver cada una por separado da
respuestas más precisas, y además se puede paralelizar.

Dos etapas, separables:

  descomponer(texto)  -> List[str]     las sub-preguntas
  responder(preguntas, modo=...)       las manda en paralelo y junta todo

La descomposición tiene dos motores:

  - `descomponer_local()`: reglas puras, sin API. Parte por los conectores
    lógicos declarados abajo. Es determinista, instantáneo y testeable
    offline — es lo que corren los tests.
  - `descomponer()`: le pide al LLM que lo haga y, si falla o no llega al
    mínimo, completa con el motor local. Nunca devuelve menos del mínimo.

El divisor es ESTRUCTURAL: no descarta cláusulas ni juzga de qué hablan.
Si está en el texto de entrada, sale en la lista.

Modos de envío (`modo`):
  'deepseek'  todas las sub-preguntas a DeepSeek (default)
  'claude'    todas a Claude
  'combinado' se reparten entre ambos, alternando (rápido y más barato)
  'comparar'  cada sub-pregunta va a los DOS, para contrastar respuestas
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.proveedores import crear_proveedor, ErrorProveedor

# No hay tope de largo. Los textos que superan PALABRAS_POR_TROZO se parten en
# trozos (siempre cortando entre oraciones, nunca al medio de una) y cada trozo
# se descompone por separado; después se juntan y se deduplican.
#
# Trocear no es solo para no chocar con el contexto del modelo: una sola pasada
# sobre 3000 palabras devuelve preguntas cada vez más genéricas hacia el final,
# porque el modelo pierde el detalle. Por trozos, cada parte recibe la misma
# atención.
PALABRAS_POR_TROZO = 400
MAX_TROZOS = 40                 # ~16.000 palabras; más que eso, avisa
MINIMO_PREGUNTAS = 6
MAX_HILOS = 6

# Conectores lógicos por los que se corta una frase. El orden importa: los
# multi-palabra van primero para que 'a través de' no se parta por 'de'.
CONECTORES_ES = [
    "a través de", "por medio de", "siempre que", "dado que", "ya que",
    "mediante", "además", "también", "mientras", "cuando", "porque",
    "usando", "para", "con", "si", "y",
]
CONECTORES_EN = [
    "by means of", "through", "as long as", "given that", "because",
    "while", "when", "also", "besides", "using", "with", "and", "if", "for",
]

# Arranques que ya indican que el fragmento es una pregunta.
INTERROGATIVOS_ES = ("qué", "que", "cómo", "como", "cuándo", "cuando", "dónde",
                     "donde", "por qué", "porqué", "cuál", "cual", "cuáles",
                     "cuales", "quién", "quien", "cuánto", "cuanto")
INTERROGATIVOS_EN = ("what", "how", "when", "where", "why", "which", "who",
                     "whose", "whom")

PALABRAS_ES = {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una",
               "se", "con", "para", "por", "cómo", "qué", "es", "del", "al"}
PALABRAS_EN = {"the", "of", "and", "to", "in", "is", "how", "what", "a", "an",
               "for", "with", "on", "are", "does", "do"}

# Plantillas para completar hasta el mínimo cuando el texto no da para tanto.
PLANTILLAS_ES = [
    "¿Qué es {t}?",
    "¿Cómo se instala {t}?",
    "¿Cómo se configura {t}?",
    "¿Cómo se valida que {t} funcione correctamente?",
    "¿Cuáles son los errores más comunes con {t}?",
    "¿Qué requisitos previos tiene {t}?",
]
PLANTILLAS_EN = [
    "What is {t}?",
    "How is {t} installed?",
    "How is {t} configured?",
    "How can you verify that {t} works correctly?",
    "What are the most common errors with {t}?",
    "What are the prerequisites for {t}?",
]


class ErrorProcesador(ValueError):
    """Entrada inválida (vacía o más larga que el límite)."""


# --- Descomposición ---------------------------------------------------------

def _idioma(texto: str) -> str:
    palabras = set(re.findall(r"[a-záéíóúñü]+", texto.lower()))
    return "es" if len(palabras & PALABRAS_ES) >= len(palabras & PALABRAS_EN) else "en"


def _validar(texto: str) -> str:
    if not isinstance(texto, str) or not texto.strip():
        raise ErrorProcesador("El texto de entrada está vacío")
    palabras = len(texto.split())
    tope = PALABRAS_POR_TROZO * MAX_TROZOS
    if palabras > tope:
        raise ErrorProcesador(
            f"El texto tiene {palabras:,} palabras y el tope es {tope:,} "
            f"({MAX_TROZOS} trozos). Partilo en dos y procesalos por separado."
        )
    return texto.strip()


def trocear(texto: str, palabras_por_trozo: int = PALABRAS_POR_TROZO) -> List[str]:
    """Parte el texto en trozos de ~N palabras, cortando SIEMPRE entre oraciones.

    Cortar al medio de una oración partiría una intención en dos y generaría dos
    sub-preguntas incompletas, que es justo lo que este módulo evita.
    """
    oraciones = [o for o in re.split(r"(?<=[.?!])\s+|\n{2,}", texto.strip()) if o.strip()]
    if not oraciones:
        return [texto.strip()]

    trozos, actual, cuenta = [], [], 0
    for oracion in oraciones:
        n = len(oracion.split())
        # Una oración sola más larga que el trozo va igual: no se parte.
        if actual and cuenta + n > palabras_por_trozo:
            trozos.append(" ".join(actual))
            actual, cuenta = [], 0
        actual.append(oracion)
        cuenta += n
    if actual:
        trozos.append(" ".join(actual))
    return trozos


# Nombres técnicos: siglas (SSL, DAG), CamelCase (BigQuery), alfanuméricos
# (OAuth2, S3), rutas (/billing), dotted (boto3.client) y `código`.
PATRON_TERMINO = re.compile(
    r"`([^`]+)`"                       # entre backticks
    r"|(/[A-Za-z][\w/-]*)"             # rutas tipo /billing
    r"|(\b[A-Za-z_][\w.]*\(\))"        # llamadas tipo funcion()
    r"|(\b[A-Z][A-Za-z0-9_.+-]*\b)"    # Mayúscula inicial, siglas, CamelCase
)


def _terminos_clave(texto: str) -> List[str]:
    """Términos técnicos del texto.

    Descarta la palabra que abre una oración ('Necesito…', 'How…'): está en
    mayúscula por posición, no porque sea un nombre propio. Se la queda igual
    si es una sigla, tiene dígitos o puntos, o reaparece en medio de una frase.
    """
    inicios_de_oracion = {
        m.start() for m in re.finditer(r"(?:^|[.?!¿¡]\s*)\s*", texto)
    }
    candidatos: List[Tuple[str, int]] = []
    for m in PATRON_TERMINO.finditer(texto):
        valor = next(g for g in m.groups() if g)
        candidatos.append((valor, m.start()))

    # Un término es "de verdad" si aparece alguna vez fuera de inicio de oración.
    fuera_de_inicio = {
        v.lower() for v, pos in candidatos if pos not in inicios_de_oracion
    }

    vistos, salida = set(), []
    for valor, pos in candidatos:
        clave = valor.lower()
        if clave in PALABRAS_ES or clave in PALABRAS_EN or clave in vistos:
            continue
        parece_tecnico = (valor.isupper() or any(c.isdigit() for c in valor)
                          or "." in valor or "/" in valor or "(" in valor
                          or not valor[1:].islower())          # CamelCase
        if pos in inicios_de_oracion and not parece_tecnico and clave not in fuera_de_inicio:
            continue
        vistos.add(clave)
        salida.append(valor)
    return salida


# El conector que precede a un fragmento dice qué se está preguntando sobre él.
# 'cuando falla un DAG' no es "¿cómo se falla un DAG?" sino "¿qué pasa cuando
# falla un DAG?". Cada conector trae su molde.
MOLDES_ES = {
    "cuando": "¿Qué pasa cuando {f}?",
    "mientras": "¿Qué pasa mientras {f}?",
    "si": "¿Qué pasa si {f}?",
    "siempre que": "¿Qué pasa siempre que {f}?",
    "porque": "¿Por qué {f}?",
    "dado que": "¿Por qué importa que {f}?",
    "ya que": "¿Por qué importa que {f}?",
    "para": "¿Cómo se hace para {f}?",
    "usando": "¿Cómo se usa {f}?",
    "mediante": "¿Cómo se usa {f}?",
    "con": "¿Cómo se usa {f}?",
    "a través de": "¿Cómo se usa {f}?",
    "por medio de": "¿Cómo se usa {f}?",
}
MOLDES_EN = {
    "when": "What happens when {f}?",
    "while": "What happens while {f}?",
    "if": "What happens if {f}?",
    "as long as": "What happens as long as {f}?",
    "because": "Why {f}?",
    "given that": "Why does it matter that {f}?",
    "for": "How do you achieve {f}?",
    "using": "How do you use {f}?",
    "by means of": "How do you use {f}?",
    "with": "How do you use {f}?",
    "through": "How do you use {f}?",
}

# Arranques que no aportan (relleno de conexión o intención en primera persona).
# Ojo: acá NO van artículos ('el', 'the', 'a'). Son parte del sintagma, y
# sacarlos rompe la gramática ('a node fails' -> 'node fails').
RUIDO_INICIAL_ES = re.compile(
    r"^(?:de|que|del)\s+|"
    r"^(?:necesito|quiero|quisiera|me gustar[ií]a|busco|preciso)\s+"
    r"(?:entender|saber|conocer|ver)?\s*", re.IGNORECASE)
RUIDO_INICIAL_EN = re.compile(
    r"^(?:of|that)\s+|"
    r"^(?:i need|i want|i'd like|we need|we want)\s+(?:to\s+)?"
    r"(?:understand|know|see)?\s*", re.IGNORECASE)


def _a_pregunta(fragmento: str, idioma: str, conector: str = "") -> str:
    """Convierte un fragmento en una pregunta gramaticalmente independiente.

    `conector` es el nexo que lo precedía en el texto original: define el molde
    de la pregunta, porque no se pregunta lo mismo después de 'cuando' que
    después de 'usando'.
    """
    f = fragmento.strip(" ,;:.¿?¡!\n\t")
    f = re.sub(r"\s+", " ", f)
    ruido = RUIDO_INICIAL_ES if idioma == "es" else RUIDO_INICIAL_EN
    f = ruido.sub("", f, count=1).strip()
    if len(f.split()) < 2:
        return ""

    # Si el fragmento ya trae su propio interrogativo, se respeta tal cual:
    # 'cómo se renuevan los certificados' no necesita ningún molde.
    interrogativos = INTERROGATIVOS_ES if idioma == "es" else INTERROGATIVOS_EN
    if f.split()[0].lower().strip("¿") in interrogativos:
        f = f[0].upper() + f[1:]
        return f"¿{f}?" if idioma == "es" else f"{f}?"

    moldes = MOLDES_ES if idioma == "es" else MOLDES_EN
    molde = moldes.get((conector or "").lower().strip())
    if molde is None:
        # Sin conector con molde propio: pregunta neutra que siempre cierra bien.
        molde = "¿Cómo funciona {f}?" if idioma == "es" else "How does {f} work?"

    pregunta = molde.format(f=f[0].lower() + f[1:])
    # Mayúscula en la primera letra real (después del '¿' si lo hay).
    i = 1 if pregunta.startswith("¿") else 0
    return pregunta[:i] + pregunta[i].upper() + pregunta[i + 1:]


MIN_PALABRAS_CLAUSULA = 3


def _partir_por_conectores(oracion: str, idioma: str) -> List[Tuple[str, str]]:
    """Parte la oración por conectores lógicos, pero solo donde lo que queda a
    ambos lados es una cláusula de verdad.

    'Nginx con SSL' NO se parte: 'SSL' suelto no es una cláusula, es un
    complemento, y separarlo perdería información. En cambio 'configura X, y
    cómo se renuevan Y' sí se parte, porque los dos lados se sostienen solos.
    El conector se reengancha al fragmento anterior para no perder texto.
    """
    conectores = CONECTORES_ES if idioma == "es" else CONECTORES_EN
    # Largos primero para que 'a través de' no se parta por 'de'.
    patron = "|".join(re.escape(c) for c in sorted(conectores, key=len, reverse=True))
    # El grupo de captura conserva el conector en la lista resultante.
    piezas = re.split(rf"\b({patron})\b", oracion, flags=re.IGNORECASE)

    # Cada fragmento viaja con el conector que lo introdujo ('' el primero).
    fragmentos: List[List[str]] = [["", piezas[0].strip(" ,;:")]]
    for i in range(1, len(piezas) - 1, 2):
        conector, siguiente = piezas[i], piezas[i + 1].strip(" ,;:")
        if len(siguiente.split()) >= MIN_PALABRAS_CLAUSULA:
            fragmentos.append([conector, siguiente])
        else:
            # Complemento corto: vuelve a pegarse donde estaba.
            fragmentos[-1][1] = f"{fragmentos[-1][1]} {conector} {siguiente}".strip()

    return [(c, f) for c, f in fragmentos if len(f.split()) >= 2]


def descomponer_local(texto: str, minimo: int = MINIMO_PREGUNTAS) -> List[str]:
    """Descomposición por reglas, sin API. Determinista y testeable offline.

    No descarta nada: cada cláusula del texto original aparece en la salida.
    Si no se llega al mínimo, se completa con preguntas derivadas de los
    términos técnicos detectados.
    """
    texto = _validar(texto)
    idioma = _idioma(texto)

    preguntas: List[str] = []
    for oracion in re.split(r"(?<=[.?!])\s+|\n+", texto):
        oracion = oracion.strip()
        if not oracion:
            continue
        for conector, fragmento in (_partir_por_conectores(oracion, idioma)
                                    or [("", oracion)]):
            pregunta = _a_pregunta(fragmento, idioma, conector)
            if pregunta:
                preguntas.append(pregunta)

    preguntas = _sin_duplicados(preguntas)

    # Relleno: se generan desde los términos técnicos del propio texto, así
    # las preguntas artificiales siguen siendo sobre el tema y no ruido.
    if len(preguntas) < minimo:
        plantillas = PLANTILLAS_ES if idioma == "es" else PLANTILLAS_EN
        # Sin términos técnicos no se inventan plantillas: quedaría
        # '¿Qué es hace?'. Se cae al relleno neutro de más abajo.
        terminos = _terminos_clave(texto)
        for plantilla in plantillas:
            for termino in terminos:
                if len(preguntas) >= minimo:
                    break
                candidata = plantilla.format(t=termino)
                if candidata.lower() not in {p.lower() for p in preguntas}:
                    preguntas.append(candidata)
            if len(preguntas) >= minimo:
                break

    # Último recurso: si ni siquiera hay términos, numeramos aspectos.
    i = 1
    while len(preguntas) < minimo:
        extra = (f"¿Qué otros aspectos hay que tener en cuenta ({i})?"
                 if idioma == "es" else
                 f"What other aspects should be considered ({i})?")
        preguntas.append(extra)
        i += 1

    return preguntas


def _tema_generico(texto: str, idioma: str) -> str:
    palabras = [p for p in re.findall(r"\b\w{4,}\b", texto)
                if p.lower() not in PALABRAS_ES and p.lower() not in PALABRAS_EN]
    return palabras[0] if palabras else ("el tema" if idioma == "es" else "the topic")


def _sin_duplicados(preguntas: List[str]) -> List[str]:
    vistas, salida = set(), []
    for p in preguntas:
        clave = re.sub(r"\W+", "", p.lower())
        if clave and clave not in vistas:
            vistas.add(clave)
            salida.append(p)
    return salida


PROMPT_DESCOMPOSICION = """Descomponé el siguiente texto en sub-preguntas atómicas e independientes.

REGLAS:
- Cada sub-pregunta tiene que entenderse SOLA, sin leer las demás. Nada de "eso", "lo anterior", "dicho proceso": repetí el sujeto completo.
- Cortá por los conectores lógicos: {conectores}.
- No descartes ninguna parte del texto: toda intención presente tiene que aparecer.
- Mínimo {minimo} preguntas. Si el texto da para menos, agregá preguntas del mismo tema que un desarrollador necesitaría igual (prerequisitos, instalación, validación, errores comunes).
- Respondé en el MISMO idioma del texto.

FORMATO DE SALIDA — importantísimo:
Una pregunta por línea. Sin numeración, sin guiones, sin viñetas, sin JSON, sin comentarios. Solo las preguntas, una por línea.

TEXTO:
{texto}"""


def descomponer(texto: str, minimo: int = MINIMO_PREGUNTAS,
                proveedor: Optional[Any] = None) -> List[str]:
    """Descompone con el LLM; si no hay proveedor o falla, usa las reglas.

    Siempre devuelve al menos `minimo` elementos, y siempre `List[str]`.
    """
    texto = _validar(texto)

    if proveedor is None:
        proveedor = crear_proveedor()
    if proveedor is None:
        return descomponer_local(texto, minimo)

    # Texto largo: se trocea y cada trozo se descompone en paralelo. El mínimo
    # se aplica al TOTAL, no a cada trozo (si no, un texto de 10 trozos daría
    # 60 preguntas infladas con relleno).
    trozos = trocear(texto)
    if len(trozos) > 1:
        print(f"   ✂️  {len(texto.split()):,} palabras -> {len(trozos)} trozos")
        with ThreadPoolExecutor(max_workers=min(MAX_HILOS, len(trozos))) as pool:
            listas = list(pool.map(
                lambda t: _descomponer_trozo(t, proveedor), trozos))
        preguntas = _sin_duplicados([p for lista in listas for p in lista])
        if len(preguntas) < minimo:
            for extra in descomponer_local(texto, minimo):
                if len(preguntas) >= minimo:
                    break
                preguntas.append(extra)
            preguntas = _sin_duplicados(preguntas)
        return preguntas

    idioma = _idioma(texto)
    conectores = ", ".join(CONECTORES_ES if idioma == "es" else CONECTORES_EN)
    prompt = PROMPT_DESCOMPOSICION.format(
        conectores=conectores, minimo=minimo, texto=texto)

    try:
        respuesta = proveedor.completar(
            mensajes=[
                {"role": "system", "content":
                 "Sos un preprocesador de preguntas técnicas. Devolvés solo "
                 "preguntas, una por línea, sin ningún otro texto."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
    except ErrorProveedor:
        return descomponer_local(texto, minimo)

    preguntas = _parsear_lineas(respuesta.texto)

    # Red de seguridad: el mínimo se garantiza pase lo que pase.
    if len(preguntas) < minimo:
        for extra in descomponer_local(texto, minimo):
            if len(preguntas) >= minimo:
                break
            if re.sub(r"\W+", "", extra.lower()) not in {
                    re.sub(r"\W+", "", p.lower()) for p in preguntas}:
                preguntas.append(extra)

    return _sin_duplicados(preguntas)


def _descomponer_trozo(trozo: str, proveedor: Any) -> List[str]:
    """Descompone UN trozo. Sin mínimo: el relleno se decide sobre el total."""
    idioma = _idioma(trozo)
    conectores = ", ".join(CONECTORES_ES if idioma == "es" else CONECTORES_EN)
    prompt = PROMPT_DESCOMPOSICION.format(conectores=conectores, minimo=2, texto=trozo)
    try:
        respuesta = proveedor.completar(
            mensajes=[
                {"role": "system", "content":
                 "Sos un preprocesador de preguntas técnicas. Devolvés solo "
                 "preguntas, una por línea, sin ningún otro texto."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return _parsear_lineas(respuesta.texto)
    except ErrorProveedor:
        # Un trozo que falla no arruina el resto: cae a reglas solo para él.
        try:
            return descomponer_local(trozo, 1)
        except ErrorProcesador:
            return []


def _parsear_lineas(texto: str) -> List[str]:
    """Saca numeración, viñetas y comillas de la respuesta del modelo."""
    preguntas = []
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        linea = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", linea)
        linea = linea.strip().strip('"').strip("'").strip()
        # Descartamos preámbulos del tipo 'Aquí están las preguntas:'
        if not linea or (len(linea.split()) < 3 and not linea.endswith("?")):
            continue
        preguntas.append(linea)
    return _sin_duplicados(preguntas)


# --- Envío en paralelo ------------------------------------------------------

@dataclass
class Respuesta:
    pregunta: str
    texto: str = ""
    proveedor: str = ""
    segundos: float = 0.0
    error: str = ""


@dataclass
class Resultado:
    texto_original: str
    preguntas: List[str] = field(default_factory=list)
    respuestas: List[Respuesta] = field(default_factory=list)
    modo: str = ""
    segundos_total: float = 0.0

    @property
    def fallidas(self) -> List[Respuesta]:
        return [r for r in self.respuestas if r.error]

    def como_texto(self) -> str:
        lineas = [f"{len(self.preguntas)} sub-preguntas · modo '{self.modo}' · "
                  f"{self.segundos_total:.1f}s", ""]
        for i, r in enumerate(self.respuestas, 1):
            lineas.append(f"{i}. {r.pregunta}   [{r.proveedor}, {r.segundos:.1f}s]")
            lineas.append(f"   {r.error or r.texto}")
            lineas.append("")
        return "\n".join(lineas)


def _proveedores_para(modo: str) -> Tuple[List[Any], List[str]]:
    """Devuelve (proveedores, avisos) según el modo pedido."""
    modo = (modo or "deepseek").lower()
    avisos: List[str] = []

    if modo in ("deepseek", "claude", "qwen", "gemini"):
        p = crear_proveedor(modo)
        if p is None:
            raise ErrorProcesador(
                f"No pude usar '{modo}': faltan credenciales en el .env")
        return [p], avisos

    if modo in ("combinado", "comparar", "ambos"):
        ds, cl = crear_proveedor("deepseek"), crear_proveedor("claude")
        disponibles = [p for p in (ds, cl) if p is not None]
        if not disponibles:
            raise ErrorProcesador(
                "No hay ningún proveedor disponible: configurá DEEPSEEK_API_KEY "
                "o ANTHROPIC_API_KEY en el .env")
        if len(disponibles) == 1:
            avisos.append(f"Solo hay uno configurado ({disponibles[0].nombre}); "
                          f"el modo '{modo}' se degrada a ese.")
        return disponibles, avisos

    raise ErrorProcesador(
        f"Modo desconocido: '{modo}'. Usá deepseek, claude, qwen, gemini, combinado o comparar.")


def _preguntar(proveedor: Any, pregunta: str, contexto: str) -> Respuesta:
    inicio = time.time()
    try:
        r = proveedor.completar(
            mensajes=[
                {"role": "system", "content":
                 "Respondé de forma técnica, concreta y breve. Es una "
                 "sub-pregunta de una consulta más grande; contestá solo esta."},
                {"role": "user", "content":
                 (f"Contexto de la consulta original:\n{contexto}\n\n"
                  f"Pregunta puntual a responder:\n{pregunta}")},
            ],
            temperature=0.3,
        )
        return Respuesta(pregunta=pregunta, texto=r.texto.strip(),
                         proveedor=proveedor.nombre, segundos=time.time() - inicio)
    except ErrorProveedor as e:
        return Respuesta(pregunta=pregunta, proveedor=proveedor.nombre,
                         segundos=time.time() - inicio, error=str(e))
    except Exception as e:                      # noqa: BLE001 - un hilo no debe tumbar el lote
        return Respuesta(pregunta=pregunta, proveedor=getattr(proveedor, "nombre", "?"),
                         segundos=time.time() - inicio, error=f"error inesperado: {e}")


def responder(preguntas: List[str], modo: str = "deepseek",
              contexto: str = "", max_hilos: int = MAX_HILOS) -> List[Respuesta]:
    """Manda las sub-preguntas a la IA en paralelo y devuelve las respuestas
    en el mismo orden que entraron."""
    if not preguntas:
        return []

    proveedores, _ = _proveedores_para(modo)
    comparar = (modo or "").lower() == "comparar"

    # (proveedor, pregunta) para cada llamada que hay que hacer.
    tareas: List[Tuple[Any, str]] = []
    for i, pregunta in enumerate(preguntas):
        if comparar:
            tareas += [(p, pregunta) for p in proveedores]
        else:
            # 'combinado' alterna; con un solo proveedor esto es un no-op.
            tareas.append((proveedores[i % len(proveedores)], pregunta))

    with ThreadPoolExecutor(max_workers=max(1, min(max_hilos, len(tareas)))) as pool:
        return list(pool.map(lambda t: _preguntar(t[0], t[1], contexto), tareas))


def procesar(texto: str, modo: str = "deepseek", minimo: int = MINIMO_PREGUNTAS,
             max_hilos: int = MAX_HILOS, solo_descomponer: bool = False) -> Resultado:
    """Pipeline completo: descompone y (salvo que pidas lo contrario) responde."""
    inicio = time.time()
    texto = _validar(texto)

    preguntas = descomponer(texto, minimo)
    resultado = Resultado(texto_original=texto, preguntas=preguntas, modo=modo)

    if not solo_descomponer:
        resultado.respuestas = responder(preguntas, modo=modo, contexto=texto,
                                         max_hilos=max_hilos)

    resultado.segundos_total = time.time() - inicio
    return resultado


# --- Como herramienta del chat ---------------------------------------------

# Tope defensivo: el modelo podría pedir 'comparar' sobre 20 sub-preguntas y
# eso son 40 llamadas. Desde el chat se acota.
MAX_PREGUNTAS_TOOL = 12
MAX_CHARS_RESPUESTA_TOOL = 700


def ejecutar_tool_procesador(nombre_tool: str, argumentos: dict) -> Dict[str, Any]:
    if nombre_tool != "descomponer_pregunta":
        return {"error": f"Herramienta de procesador desconocida: {nombre_tool}"}

    texto = (argumentos or {}).get("texto", "")
    modo = (argumentos or {}).get("modo", "") or ""
    try:
        minimo = int((argumentos or {}).get("minimo", MINIMO_PREGUNTAS))
    except (TypeError, ValueError):
        minimo = MINIMO_PREGUNTAS
    minimo = max(2, min(minimo, MAX_PREGUNTAS_TOOL))

    try:
        preguntas = descomponer(texto, minimo)
    except ErrorProcesador as e:
        return {"error": str(e)}

    # Sin modo, solo descompone: es barato y muchas veces alcanza, porque el
    # propio chat puede contestar las sub-preguntas con lo que ya sabe.
    if not modo:
        return {"preguntas": preguntas, "total": len(preguntas)}

    preguntas = preguntas[:MAX_PREGUNTAS_TOOL]
    try:
        respuestas = responder(preguntas, modo=modo, contexto=texto)
    except ErrorProcesador as e:
        return {"preguntas": preguntas, "total": len(preguntas), "error": str(e)}

    return {
        "preguntas": preguntas,
        "total": len(preguntas),
        "modo": modo,
        "respuestas": [
            {"pregunta": r.pregunta, "proveedor": r.proveedor,
             **({"error": r.error} if r.error
                else {"respuesta": r.texto[:MAX_CHARS_RESPUESTA_TOOL]})}
            for r in respuestas
        ],
    }


TOOLS_SCHEMA_PROCESADOR: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "descomponer_pregunta",
            "description": (
                "Parte una consulta larga o enredada en sub-preguntas atómicas e "
                "independientes (mínimo 6), cortando por conectores lógicos. Usala "
                "cuando la persona te trae un párrafo con varias preguntas mezcladas "
                "y conviene atacarlas de a una. Si NO pasás 'modo', solo te devuelve "
                "la lista de sub-preguntas y las contestás vos mismo — eso es lo "
                "normal y no gasta llamadas extra. Pasá 'modo' solo si conviene que "
                "cada sub-pregunta se resuelva por separado y en paralelo, o si la "
                "persona pidió expresamente comparar modelos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string", "description": "El texto a descomponer (máx. 500 palabras)"},
                    "minimo": {"type": "integer", "description": f"Mínimo de sub-preguntas. Default {MINIMO_PREGUNTAS}, tope {MAX_PREGUNTAS_TOOL}"},
                    "modo": {
                        "type": "string",
                        "enum": ["deepseek", "claude", "combinado", "comparar"],
                        "description": ("Si lo pasás, además de descomponer manda cada sub-pregunta "
                                        "a la IA en paralelo. 'combinado' reparte entre DeepSeek y "
                                        "Claude; 'comparar' consulta a los dos por cada pregunta. "
                                        "Omitilo para solo descomponer.")
                    }
                },
                "required": ["texto"]
            }
        }
    }
]


# --- Ejemplos de uso --------------------------------------------------------

if __name__ == "__main__":
    ejemplo = "¿Cómo se configura Nginx con SSL, y cómo se renuevan los certificados?"

    print("Entrada:", ejemplo, "\n")
    print("descomponer_local() — sin API, determinista:")
    for i, p in enumerate(descomponer_local(ejemplo), 1):
        print(f"  {i}. {p}")

    print("\nEs List[str]:", type(descomponer_local(ejemplo)).__name__,
          "de", type(descomponer_local(ejemplo)[0]).__name__)

    # Con API configurada, el pipeline completo sería:
    #     r = procesar(ejemplo, modo="combinado")
    #     print(r.como_texto())
