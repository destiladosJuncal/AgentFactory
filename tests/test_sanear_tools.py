"""Tests del saneador de secuencias de tool calls (core.chat.sanear_secuencia_tools).

El contrato de las APIs tipo OpenAI/DeepSeek es estricto: cada mensaje 'assistant'
con tool_calls tiene que ir seguido, inmediatamente, de un 'tool' por cada id que
pidió; y no puede haber 'tool' sueltos. Si el historial se ensucia (tool-calls en
paralelo, una interrupción a mitad de ronda, respuestas que llegan tarde), la
siguiente llamada se cae con un 400. El saneador deja la secuencia siempre válida.
"""

from core.chat import sanear_secuencia_tools


def _validar(seq):
    """Devuelve la lista de violaciones del contrato (vacía = válido)."""
    errores = []
    esperados = []
    for i, m in enumerate(seq):
        rol = m.get("role")
        if rol == "tool":
            tcid = m.get("tool_call_id")
            if tcid not in esperados:
                errores.append(f"idx {i}: tool '{tcid}' sin tool_calls previo")
            else:
                esperados.remove(tcid)
        else:
            if esperados:
                errores.append(f"idx {i}: {rol} con tool_calls sin responder {esperados}")
            esperados = ([tc["id"] for tc in (m.get("tool_calls") or [])]
                         if rol == "assistant" else [])
    if esperados:
        errores.append(f"final: tool_calls sin responder {esperados}")
    return errores


def _asist(ids):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": i} for i in ids]}


def _tool(tcid, contenido="ok"):
    return {"role": "tool", "tool_call_id": tcid, "content": contenido}


def test_historial_limpio_no_cambia():
    msgs = [
        {"role": "user", "content": "hola"},
        _asist(["a1"]),
        _tool("a1"),
        {"role": "assistant", "content": "listo"},
    ]
    assert sanear_secuencia_tools(msgs) == msgs
    assert _validar(sanear_secuencia_tools(msgs)) == []


def test_descarta_tool_huerfano():
    # Un 'tool' sin ningún assistant que lo pida (p. ej. llegó tras una interrupción).
    msgs = [
        {"role": "user", "content": "busca"},
        _tool("fantasma"),
        {"role": "assistant", "content": "ya está"},
    ]
    out = sanear_secuencia_tools(msgs)
    assert _validar(out) == []
    assert all(m.get("tool_call_id") != "fantasma" for m in out)
    # El user y el assistant-texto se conservan.
    assert out[0]["content"] == "busca"
    assert out[-1]["content"] == "ya está"


def test_reordena_respuestas_fuera_de_orden():
    # Dos assistant tool_calls seguidos con respuestas intercaladas (tool-calls en
    # paralelo): hay que pegar cada respuesta a su assistant.
    msgs = [
        _asist(["A", "B"]),
        _asist(["C"]),
        _tool("C"),
        _tool("A"),
        _tool("B"),
    ]
    assert _validar(msgs)  # el original es inválido
    out = sanear_secuencia_tools(msgs)
    assert _validar(out) == []
    # El primer assistant queda seguido de A y B; el segundo, de C.
    assert out[0]["tool_calls"][0]["id"] == "A"
    assert {out[1]["tool_call_id"], out[2]["tool_call_id"]} == {"A", "B"}
    assert out[3]["tool_calls"][0]["id"] == "C"
    assert out[4]["tool_call_id"] == "C"


def test_rellena_tool_calls_sin_respuesta():
    # Un assistant pidió dos tools pero solo volvió una (la otra se interrumpió).
    msgs = [_asist(["x", "y"]), _tool("x")]
    out = sanear_secuencia_tools(msgs)
    assert _validar(out) == []
    relleno = [m for m in out if m.get("tool_call_id") == "y"]
    assert len(relleno) == 1
    assert "sin respuesta" in relleno[0]["content"]


def test_es_idempotente():
    msgs = [
        {"role": "user", "content": "x"},
        _asist(["A", "B"]),
        _asist(["C"]),
        _tool("C"), _tool("A"), _tool("B"),
        {"role": "user", "content": "y"},
    ]
    una = sanear_secuencia_tools(msgs)
    dos = sanear_secuencia_tools(una)
    assert una == dos
    assert _validar(una) == []


def test_no_muta_la_lista_original():
    msgs = [_asist(["A"]), _tool("A")]
    copia = [dict(m) for m in msgs]
    sanear_secuencia_tools(msgs)
    assert msgs == copia
