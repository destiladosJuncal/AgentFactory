#!/usr/bin/env python3
"""
Phase-0 demo: two agents collaborating against the configured provider.

Runs a small, self-contained task through a Crew of two agents (a researcher who
drafts, a reviewer who refines) and prints the hand-off log and the final output,
so you can see the collaboration end to end.

    <python> crew_demo.py [--keep] ["your task here"]

Kept deliberately cheap: the demo agents run WITHOUT tools (pure conversation),
so no shell, library or web calls fire — it validates the collaboration wiring,
not the tool stack. By default it deletes the two demo conversations it creates
so your conversation list stays clean; pass --keep to inspect them in the app.
"""

import shutil
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from core import rutas
rutas.cargar_env()

from core import consola
consola.setup_utf8()

from core.crew import Agent, Crew


DEFAULT_TASK = (
    "En 3 bullets, explicá qué es un proxy de captura de tráfico y para qué "
    "sirve dentro de una app que genera agentes web. Sé concreto y breve."
)


def _short(text: str, n: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " […]"


def main():
    args = [a for a in sys.argv[1:]]
    keep = "--keep" in args
    args = [a for a in args if a != "--keep"]
    task = args[0] if args else DEFAULT_TASK

    from core.proveedores import crear_proveedor, proveedor_configurado
    provider = crear_proveedor()
    if provider is None:
        print(f"○ Modo simulación (proveedor '{proveedor_configurado()}' sin "
              f"credenciales): las respuestas serán un placeholder, pero el "
              f"hand-off entre agentes se ve igual.\n")
    else:
        print(f"● Proveedor: {provider.descripcion()}\n")

    print(f"TAREA: {task}\n" + "=" * 70)

    # Tools off: pure conversation, cheap and predictable for a first run.
    researcher = Agent(
        "Rae", role="investigadora",
        goal="producir un primer borrador claro y correcto",
        tools_enabled=False)
    reviewer = Agent(
        "Val", role="revisor",
        goal="corregir, recortar y dejar la versión final pulida",
        tools_enabled=False)

    crew = Crew([researcher, reviewer], name="demo")
    run = crew.run(task)

    # Hand-off log: who passed what to whom.
    print("\nHAND-OFFS")
    print("-" * 70)
    for i, h in enumerate(run.handoffs, 1):
        origin = h.from_agent or "· tarea inicial ·"
        print(f"{i}. {origin}  →  {h.to_agent}")
        print(f"   {_short(h.content, 300)}\n")

    # Per-agent usage (best-effort).
    print("USO POR AGENTE")
    print("-" * 70)
    for agent in crew.agents:
        u = agent.last_usage or {}
        if u:
            print(f"· {agent.name}: {u.get('entrada', 0)} in / "
                  f"{u.get('salida', 0)} out tokens · "
                  f"US$ {u.get('costo', 0):.4f} · {u.get('llamadas', 0)} llamada(s)")
        else:
            print(f"· {agent.name}: (sin datos de uso)")

    print("\nRESULTADO FINAL")
    print("=" * 70)
    print(run.final_output)

    # Cleanup: remove the two demo conversations unless asked to keep them.
    if keep:
        print("\n(--keep) Conversaciones del demo conservadas:")
        for agent in crew.agents:
            print(f"  {agent.conversation_dir}")
    else:
        for agent in crew.agents:
            d = agent.conversation_dir
            if d and Path(d).is_dir():
                shutil.rmtree(d, ignore_errors=True)
        print("\n(Conversaciones del demo borradas. Usá --keep para conservarlas.)")


if __name__ == "__main__":
    main()
