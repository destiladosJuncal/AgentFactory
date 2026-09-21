#!/usr/bin/env python3
import argparse
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

from core.proyectos import GestorProyectos, AGENT_CODE_DIR
from core.agente_interactivo import AgenteInteractivo, OBJETIVOS_DEFAULT
from core.biblioteca import Biblioteca
from core.proveedores import proveedor_configurado

from core import consola  # noqa: E402
consola.setup_utf8()



def pedir_float(mensaje, default):
    valor = input(f"{mensaje} [{default}]: ").strip()
    if not valor:
        return default
    try:
        return float(valor)
    except ValueError:
        print("⚠️  Valor inválido, usando el default")
        return default


def pedir_int(mensaje, default):
    valor = input(f"{mensaje} [{default}]: ").strip()
    if not valor:
        return default
    try:
        return int(valor)
    except ValueError:
        print("⚠️  Valor inválido, usando el default")
        return default


def mostrar_proyectos(proyectos):
    if not proyectos:
        print("\n(No hay proyectos todavía)")
        return
    print(f"\n📂 Proyectos en {AGENT_CODE_DIR}:\n")
    for i, p in enumerate(proyectos, 1):
        estado = "✅" if p['exito'] else "🔄"
        print(f"  {i}. {estado} {p['nombre']}")
        print(f"     {p['descripcion'][:70]}")
        print(f"     Iteraciones: {p['iteraciones']} | Mejor puntaje: {p['mejor_puntaje']:.1%} | Actualizado: {p['actualizado']}")


def crear_proyecto_nuevo(gestor: GestorProyectos) -> Path:
    print("\n📝 Describí lo que querés que el agente construya:")
    descripcion = input("👉 Objetivo: ").strip()
    while not descripcion:
        descripcion = input("👉 Objetivo (no puede estar vacío): ").strip()

    print("\n⚙️  Configuración (Enter para usar el default)")
    umbral = pedir_float("Umbral de éxito (0-1)", 0.75)
    max_iter = pedir_int("Máximo de iteraciones", 15)

    proyecto_dir = gestor.crear_proyecto(
        descripcion=descripcion,
        objetivos=OBJETIVOS_DEFAULT,
        umbral_global=umbral,
        max_iteraciones=max_iter
    )
    print(f"\n✅ Proyecto creado en: {proyecto_dir}")
    return proyecto_dir


def elegir_proyecto_existente(proyectos):
    mostrar_proyectos(proyectos)
    while True:
        opcion = input("\n👉 Número del proyecto a retomar (o Enter para volver): ").strip()
        if opcion == "":
            return None
        try:
            idx = int(opcion) - 1
            if 0 <= idx < len(proyectos):
                return proyectos[idx]['path']
        except ValueError:
            pass
        print("❌ Opción inválida")


def mostrar_biblioteca():
    biblioteca = Biblioteca()
    resultado = biblioteca.listar()
    modulos = resultado.get('modulos', [])
    print(f"\n📚 Biblioteca compartida ({biblioteca.base_dir}):\n")
    if not modulos:
        print("   (vacía por ahora — se va a ir llenando a medida que el agente")
        print("    construya herramientas reutilizables en distintos proyectos)")
        return
    for m in modulos:
        deps = f" | depende de: {', '.join(m['dependencias'])}" if m.get('dependencias') else ""
        print(f"  • {m['nombre']}{deps}")
        print(f"    {m['descripcion']}")


def menu_inicio(gestor: GestorProyectos) -> Path:
    while True:
        proyectos = gestor.listar_proyectos()
        print("\n" + "="*50)
        print("🤖 AGENTE DE CÓDIGO")
        print("="*50)
        print(f"📁 Carpeta de trabajo: {AGENT_CODE_DIR}")
        print(f"📂 Proyectos existentes: {len(proyectos)}")
        print("\n1. Nuevo proyecto")
        print("2. Retomar proyecto")
        print("3. Listar proyectos")
        print("4. Ver biblioteca de herramientas compartidas")
        print("5. Salir")

        opcion = input("\n👉 Elegí una opción (1-5): ").strip()

        if opcion == "1":
            return crear_proyecto_nuevo(gestor)
        elif opcion == "2":
            if not proyectos:
                print("\n⚠️  No hay proyectos para retomar todavía.")
                continue
            elegido = elegir_proyecto_existente(proyectos)
            if elegido:
                return elegido
        elif opcion == "3":
            mostrar_proyectos(proyectos)
        elif opcion == "4":
            mostrar_biblioteca()
        elif opcion == "5":
            print("\n👋 ¡Hasta la próxima!")
            sys.exit(0)
        else:
            print("❌ Opción inválida")


def main():
    parser = argparse.ArgumentParser(
        description="🤖 Agente iterativo de código con proyectos persistentes"
    )
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--no-clean', action='store_true')
    parser.add_argument('--no-interact', action='store_true', help='Desactivar interacción humana')
    parser.add_argument('--cada', type=int, default=3, help='Cada cuántas iteraciones preguntar')
    parser.add_argument('--proyecto', help='Nombre de un proyecto existente, salteando el menú')
    parser.add_argument('--extender', type=int, default=0,
                         help='Si el proyecto retomado ya llegó al límite de iteraciones, cuántas agregar')

    args = parser.parse_args()

    from core.rutas import cargar_env
    cargar_env()

    print("🤖 Agente Generador de Código con Proyectos Persistentes")
    print(f"📁 Los proyectos se guardan en: {AGENT_CODE_DIR}")
    print(f"🧠 Proveedor: {proveedor_configurado()}")

    gestor = GestorProyectos()

    if args.proyecto:
        proyecto_dir = gestor.cargar_proyecto(args.proyecto)
        if not proyecto_dir:
            print(f"❌ No existe el proyecto '{args.proyecto}' en {AGENT_CODE_DIR}")
            sys.exit(1)
    else:
        proyecto_dir = menu_inicio(gestor)

    try:
        agente = AgenteInteractivo(proyecto_dir)
        agente.cada_n_iteraciones = max(1, args.cada)

        if args.no_interact:
            agente.interaccion_activa = False

        # Si estamos retomando un proyecto que ya llegó al límite sin éxito,
        # el bucle no correría ninguna iteración nueva a menos que extendamos
        # el límite acá.
        if not agente.objetivos_alcanzados and agente.iteracion >= agente.max_iteraciones:
            if args.extender > 0:
                agente.max_iteraciones += args.extender
                print(f"🔼 Límite extendido a {agente.max_iteraciones} iteraciones (--extender {args.extender})")
            elif not args.no_interact:
                print(f"\n⚠️  Este proyecto ya llegó a su límite de {agente.max_iteraciones} iteraciones sin alcanzar el objetivo.")
                try:
                    extra = input("👉 ¿Cuántas iteraciones más querés agregar? [5]: ").strip()
                    extra = int(extra) if extra else 5
                except ValueError:
                    extra = 5
                agente.max_iteraciones += max(1, extra)
                print(f"🔼 Límite extendido a {agente.max_iteraciones} iteraciones")
            else:
                print(f"\n⚠️  Este proyecto ya llegó a su límite de iteraciones. Usá --extender N para agregar más.")

        resultado = agente.ejecutar()

        print("\n" + "=" * 50)
        print("📊 RESULTADO")
        print("=" * 50)
        print(f"📁 Proyecto: {resultado['proyecto_dir']}")

        if resultado['exito']:
            print("✅ ¡OBJETIVO ALCANZADO!")
        else:
            print("⏸️  Sesión terminada sin alcanzar el umbral")
            print("   Podés retomarla con: --proyecto <nombre>")

        print(f"   Iteraciones: {resultado['iteraciones']}")
        print(f"   Mejor puntaje: {resultado['mejor_puntaje']:.2%}")

        if args.verbose:
            print("\n📊 Historial de iteraciones:")
            for idx, h in enumerate(resultado['historial'], 1):
                puntaje = h['metricas']['puntaje_global']
                barra = '█' * int(puntaje * 20) + '░' * (20 - int(puntaje * 20))
                print(f"   {idx}. [{barra}] {puntaje:.2%}")
                if h.get('diagnostico'):
                    print(f"      💬 {h['diagnostico']}")

        if not args.no_clean:
            agente.limpiar()

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrumpido. Tu progreso quedó guardado en disco, podés retomarlo después.")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
