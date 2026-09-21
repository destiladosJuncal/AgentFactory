#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from core.conversaciones import GestorConversaciones, CONVERSACIONES_DIR
from core.chat import ConversacionChat
from core.proveedores import proveedor_configurado

from core import consola  # noqa: E402
consola.setup_utf8()



def mostrar_conversaciones(conversaciones):
    if not conversaciones:
        print("\n(No hay conversaciones todavía)")
        return
    print(f"\n💬 Conversaciones en {CONVERSACIONES_DIR}:\n")
    for i, c in enumerate(conversaciones, 1):
        print(f"  {i}. {c['titulo']}")
        print(f"     Mensajes: {c['mensajes']} | Actualizado: {c['actualizado']}")


def elegir_conversacion(conversaciones):
    mostrar_conversaciones(conversaciones)
    while True:
        opcion = input("\n👉 Número de conversación a retomar (o Enter para volver): ").strip()
        if opcion == "":
            return None
        try:
            idx = int(opcion) - 1
            if 0 <= idx < len(conversaciones):
                return conversaciones[idx]['path']
        except ValueError:
            pass
        print("❌ Opción inválida")


def menu_inicio(gestor: GestorConversaciones) -> Path:
    while True:
        conversaciones = gestor.listar_conversaciones()
        print("\n" + "=" * 50)
        print("🤖 CONVERSACIÓN CON EL AGENTE")
        print("=" * 50)
        print(f"📁 Conversaciones guardadas en: {CONVERSACIONES_DIR}")
        print(f"💬 Conversaciones existentes: {len(conversaciones)}")
        print("\n1. Nueva conversación")
        print("2. Retomar conversación")
        print("3. Listar conversaciones")
        print("4. Salir")

        opcion = input("\n👉 Elegí una opción (1-4): ").strip()

        if opcion == "1":
            titulo = input("👉 Nombre para esta conversación (Enter para uno automático): ").strip()
            return gestor.crear_conversacion(titulo)
        elif opcion == "2":
            if not conversaciones:
                print("\n⚠️  No hay conversaciones para retomar todavía.")
                continue
            elegida = elegir_conversacion(conversaciones)
            if elegida:
                return elegida
        elif opcion == "3":
            mostrar_conversaciones(conversaciones)
        elif opcion == "4":
            print("\n👋 ¡Hasta la próxima!")
            sys.exit(0)
        else:
            print("❌ Opción inválida")


def mostrar_recap(conversacion: ConversacionChat, n: int = 6):
    visibles = [m for m in conversacion.mensajes if m.get('role') in ('user', 'assistant') and m.get('content')]
    if not visibles:
        return
    print("\n📜 Últimos mensajes de esta conversación:")
    print("-" * 50)
    for m in visibles[-n:]:
        quien = "Tú" if m['role'] == 'user' else "Agente"
        contenido = m['content']
        if len(contenido) > 300:
            contenido = contenido[:300] + "..."
        print(f"{quien}: {contenido}")
    print("-" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="💬 Conversación con el agente (herramientas + biblioteca compartida, historial persistente)"
    )
    parser.add_argument('--conversacion', help='Nombre de una conversación existente, salteando el menú')
    args = parser.parse_args()

    from core.rutas import cargar_env
    cargar_env()

    print(f"🤖 Agente conversacional (proveedor: {proveedor_configurado()})")

    gestor = GestorConversaciones()

    if args.conversacion:
        conversacion_dir = gestor.cargar_conversacion(args.conversacion)
        if not conversacion_dir:
            print(f"❌ No existe la conversación '{args.conversacion}' en {CONVERSACIONES_DIR}")
            sys.exit(1)
    else:
        conversacion_dir = menu_inicio(gestor)

    conversacion = ConversacionChat(conversacion_dir)

    herramientas = conversacion.herramientas_disponibles()
    n_bib = conversacion.biblioteca.listar().get('total', 0)

    print(f"\n💬 Conversación: {conversacion.meta.get('titulo', conversacion_dir.name)}")
    print(f"📂 Workspace: {conversacion.workspace_dir}")
    if herramientas:
        print(f"🔧 Herramientas disponibles: {', '.join(herramientas)}")
    else:
        print("🔧 Herramientas: desactivadas (AGENTE_TOOLS_ENABLED=0)")
    print(f"📚 Biblioteca compartida: {n_bib} módulo(s)")
    print("Escribí 'salir' para terminar (la conversación queda guardada).\n")

    mostrar_recap(conversacion)

    while True:
        try:
            texto = input("\nTú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 Conversación guardada. ¡Hasta la próxima!")
            break

        if not texto:
            continue
        if texto.lower() in ("salir", "exit", "quit"):
            print("\n👋 Conversación guardada. ¡Hasta la próxima!")
            break

        respuesta = conversacion.enviar(texto)
        print(f"\nAgente: {respuesta}")


if __name__ == "__main__":
    main()
