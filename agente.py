#!/usr/bin/env python3
"""
Punto de entrada único del agente: un menú de arranque que dirige a uno de
los dos modos, que comparten herramientas pero tienen roles distintos:

  - Proyectos     (main_interactivo.py) -> CONSTRUIR herramientas: modo
    iterativo objetivo -> generar -> ejecutar -> evaluar -> repetir.
  - Conversación  (main_chat.py)        -> EJECUTAR sobre herramientas ya
    construidas (workspace + biblioteca compartida), chat libre que
    también puede disparar 'iterar_codigo' si hace falta construir algo
    nuevo sin salir de la conversación.
"""
import argparse
import sys
from dotenv import load_dotenv

from core.proyectos import AGENT_CODE_DIR

import main_interactivo
import main_chat

from core import consola  # noqa: E402
consola.setup_utf8()



def mostrar_biblioteca():
    main_interactivo.mostrar_biblioteca()


def menu_arranque() -> str:
    while True:
        print("\n" + "=" * 50)
        print("🤖 AGENTE")
        print("=" * 50)
        print(f"📁 Carpeta de trabajo: {AGENT_CODE_DIR}")
        print("\n1. Proyectos     (construir herramientas: objetivo + iteraciones + puntaje)")
        print("2. Conversación  (hablar con el agente y ejecutar herramientas)")
        print("3. Ver biblioteca compartida de herramientas")
        print("4. Salir")

        opcion = input("\n👉 Elegí una opción (1-4): ").strip()

        if opcion == "1":
            return "proyectos"
        elif opcion == "2":
            return "conversacion"
        elif opcion == "3":
            mostrar_biblioteca()
        elif opcion == "4":
            print("\n👋 ¡Hasta la próxima!")
            sys.exit(0)
        else:
            print("❌ Opción inválida")


def main():
    parser = argparse.ArgumentParser(
        description="🤖 Agente: menú de arranque (proyectos para construir herramientas, conversación para ejecutarlas)"
    )
    parser.add_argument('--modo', choices=['proyectos', 'conversacion'],
                         help='Saltear el menú de arranque y entrar directo a este modo')
    parser.add_argument('--verbose', action='store_true',
                         help='Se reenvía al modo Proyectos si se elige/usa ese modo')
    args, resto = parser.parse_known_args()

    from core.rutas import cargar_env
    cargar_env()

    modo = args.modo or menu_arranque()

    argv_submodo = list(resto)
    if modo == "proyectos" and args.verbose:
        argv_submodo.append('--verbose')

    sys.argv = [sys.argv[0]] + argv_submodo

    if modo == "proyectos":
        main_interactivo.main()
    else:
        main_chat.main()


if __name__ == "__main__":
    main()
