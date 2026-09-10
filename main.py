#!/usr/bin/env python3
import argparse
import sys
import os
from dotenv import load_dotenv
from core.agente import AgenteCodigo


def main():
    parser = argparse.ArgumentParser(
        description="🤖 Agente iterativo de generación de código con DeepSeek"
    )
    parser.add_argument('--config', default='config/objetivos.json')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--output', help='Guardar resultado en archivo')
    parser.add_argument('--no-clean', action='store_true')

    args = parser.parse_args()

    print("🤖 Agente Generador de Código Iterativo con DeepSeek")
    print("=" * 50)

    load_dotenv()

    if not os.getenv('DEEPSEEK_API_KEY'):
        print("⚠️  ADVERTENCIA: No se encontró DEEPSEEK_API_KEY")
        print("   El agente funcionará en modo simulación")
        print("   Para usar DeepSeek, edita el archivo .env")
        print()

    try:
        agente = AgenteCodigo(args.config)
        resultado = agente.ejecutar()

        print("\n" + "=" * 50)
        print("📊 RESULTADO FINAL")
        print("=" * 50)

        if resultado['exito']:
            print("✅ ¡OBJETIVO ALCANZADO!")
            print(f"   Iteraciones: {resultado['iteraciones']}")
            print(f"   Mejor puntaje: {resultado['mejor_puntaje']:.2%}")

            if resultado['codigo_final']:
                print("\n📝 Código final:")
                print("-" * 40)
                print(resultado['codigo_final'])
                print("-" * 40)

                if args.output:
                    with open(args.output, 'w', encoding='utf-8') as f:
                        f.write(resultado['codigo_final'])
                    print(f"💾 Código guardado en: {args.output}")
        else:
            print("❌ Objetivo NO alcanzado")
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

    except FileNotFoundError:
        print(f"❌ Error: No se encontró {args.config}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrumpido por el usuario")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
