import json
import time
from pathlib import Path
from typing import Dict, Any

from core.planificador import Planificador
from core.generador import Generador
from core.ejecutor import Ejecutor
from core.evaluador import Evaluador
from core.memoria import Memoria
from core.herramientas import Herramientas
from core.biblioteca import Biblioteca

# Objetivos por defecto cuando el usuario solo da una descripción en prompt
# (sin tests formales, la funcionalidad se evalúa por ejecución exitosa).
OBJETIVOS_DEFAULT = {
    "funcionalidad": {"tests_pasados_min": 1, "tests_totales": 1},
    "eficiencia": {"tiempo_maximo_ms": 2000, "memoria_maxima_mb": 200},
    "calidad": {"puntuacion_minima": 0.7}
}


class AgenteInteractivo:
    def __init__(self, proyecto_dir: Path):
        self.proyecto_dir = Path(proyecto_dir)
        self.config_path = self.proyecto_dir / "config.json"
        self.memoria_path = self.proyecto_dir / "memoria.json"
        self.iteraciones_dir = self.proyecto_dir / "iteraciones"
        self.iteraciones_dir.mkdir(parents=True, exist_ok=True)

        self.config = json.loads(self.config_path.read_text(encoding='utf-8'))

        # Tests opcionales: si el proyecto tiene tests.json, se usan.
        # Si no, la funcionalidad se mide por ejecución exitosa (ver Evaluador).
        tests_path = self.proyecto_dir / "tests.json"
        if tests_path.exists():
            try:
                self.tests = json.loads(tests_path.read_text(encoding='utf-8'))
            except Exception:
                self.tests = []
        else:
            self.tests = []

        # Herramientas confinadas SOLO a este proyecto
        self.herramientas = Herramientas(self.proyecto_dir)

        # Biblioteca compartida entre TODOS los proyectos (fuera de este directorio)
        self.biblioteca = Biblioteca()

        self.planificador = Planificador()
        self.generador = Generador(herramientas=self.herramientas, biblioteca=self.biblioteca)
        self.ejecutor = Ejecutor()
        self.evaluador = Evaluador()
        self.memoria = Memoria()

        # Retomar historial previo si existe (sesión anterior)
        if self.memoria_path.exists():
            try:
                historial_previo = json.loads(self.memoria_path.read_text(encoding='utf-8'))
                for h in historial_previo:
                    self.memoria.guardar_iteracion(h)
            except Exception:
                pass

        self.objetivos_alcanzados = bool(self.config.get('exito', False))
        self.iteracion = len(self.memoria.obtener_historial())
        self.max_iteraciones = self.config.get('max_iteraciones', 15)
        self.feedback_usuario = ""
        self.interaccion_activa = True
        self.cada_n_iteraciones = 3

    def ejecutar(self) -> Dict[str, Any]:
        print(f"\n📁 Proyecto: {self.proyecto_dir.name}")
        print(f"📋 Objetivo: {self.config['descripcion']}")
        print(f"🎯 Umbral de éxito: {self.config['umbral_global']*100:.0f}%")
        print(f"🔄 Máximo de iteraciones: {self.max_iteraciones}")
        print(f"📂 Carpeta: {self.proyecto_dir}")
        if self.iteracion > 0 and not self.objetivos_alcanzados:
            print(f"↩️  Retomando desde la iteración {self.iteracion}")
        n_modulos = self.biblioteca.listar().get('total', 0)
        print(f"📚 Biblioteca compartida: {n_modulos} módulo(s) reutilizable(s) disponible(s)")
        print(f"💬 Interacción humana: Cada {self.cada_n_iteraciones} iteraciones\n")

        if self.objetivos_alcanzados:
            print("✅ Este proyecto ya había alcanzado el objetivo en una sesión anterior.")
            solucion = self.proyecto_dir / "solucion.py"
            if solucion.exists():
                print(f"📝 Solución guardada en: {solucion}")
            return self._resultado_final()

        while not self.objetivos_alcanzados and self.iteracion < self.max_iteraciones:
            self.iteracion += 1

            print(f"\n{'='*50}")
            print(f"🔄 Iteración {self.iteracion}/{self.max_iteraciones}")
            print(f"{'='*50}")

            plan = self.planificador.crear_plan(
                self.config['objetivos'],
                self.memoria.obtener_historial()
            )

            if self.feedback_usuario:
                plan['feedback_usuario'] = self.feedback_usuario
                print(f"💬 Feedback del usuario: {self.feedback_usuario}")

            print(f"📋 Plan: {plan['estrategia']}")

            codigo = self.generador.generar(
                plan=plan,
                historial=self.memoria.obtener_historial(),
                descripcion=self.config['descripcion'],
                feedback=self.feedback_usuario
            )
            print(f"💻 Código generado ({len(codigo)} caracteres)")

            # Persistir el código de ESTA iteración en disco de inmediato
            iter_path = self.iteraciones_dir / f"iter_{self.iteracion:03d}.py"
            iter_path.write_text(codigo, encoding='utf-8')

            resultado = self.ejecutor.ejecutar(
                codigo=codigo,
                tests=self.tests
            )
            print(f"⚙️ Ejecución: {resultado['estado']}")

            if 'tests_pasados' in resultado:
                print(f"   Tests: {resultado['tests_pasados']}/{resultado.get('tests_totales', len(self.tests))}")

            metricas = self.evaluador.evaluar(
                codigo=codigo,
                resultado=resultado,
                objetivos=self.config['objetivos']
            )
            print(f"📊 Puntaje global: {metricas['puntaje_global']:.2%}")
            print(f"   Funcionalidad: {metricas.get('puntaje_funcionalidad', 0):.2%}")
            print(f"   Eficiencia: {metricas.get('puntaje_eficiencia', 0):.2%}")
            print(f"   Calidad: {metricas.get('puntaje_calidad', 0):.2%}")

            self.memoria.guardar_iteracion({
                'iteracion': self.iteracion,
                'codigo': codigo,
                'resultado': resultado,
                'metricas': metricas,
                'diagnostico': metricas.get('diagnostico', ''),
                'razon': metricas.get('razon', '')
            })
            self._persistir_memoria()

            self.objetivos_alcanzados = self.evaluador.objetivos_cumplidos(
                metricas,
                self.config['umbral_global']
            )

            if self.objetivos_alcanzados:
                (self.proyecto_dir / "solucion.py").write_text(codigo, encoding='utf-8')
                self._actualizar_config(exito=True)
                print("\n🎉 ¡OBJETIVO ALCANZADO!")
                print(f"💾 Guardado en: {self.proyecto_dir / 'solucion.py'}")
                break

            self.feedback_usuario = ""
            self._actualizar_config()  # solo refresca 'actualizado'

            if self.iteracion % self.cada_n_iteraciones == 0 and self.interaccion_activa:
                self._interaccion_humana()

        if not self.objetivos_alcanzados and self.memoria.obtener_historial():
            ultimo_codigo = self.memoria.obtener_ultimo_codigo()
            (self.proyecto_dir / "ultima_version.py").write_text(ultimo_codigo, encoding='utf-8')
            print(f"\n💾 Última versión guardada en: {self.proyecto_dir / 'ultima_version.py'}")
            print("   (podés retomar este proyecto más tarde para seguir iterando)")

        return self._resultado_final()

    def _persistir_memoria(self):
        historial = self.memoria.obtener_historial()
        self.memoria_path.write_text(
            json.dumps(historial, indent=2, ensure_ascii=False, default=str),
            encoding='utf-8'
        )

    def _actualizar_config(self, **kwargs):
        self.config.update(kwargs)
        self.config['actualizado'] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    def _resultado_final(self) -> Dict[str, Any]:
        return {
            'exito': self.objetivos_alcanzados,
            'iteraciones': self.iteracion,
            'codigo_final': self.memoria.obtener_ultimo_codigo() if self.objetivos_alcanzados else None,
            'historial': self.memoria.obtener_historial(),
            'mejor_puntaje': self.memoria.obtener_mejor_puntaje(),
            'proyecto_dir': str(self.proyecto_dir)
        }

    def _interaccion_humana(self):
        print("\n" + "="*50)
        print("💬 INTERACCIÓN CON EL USUARIO")
        print("="*50)

        historial = self.memoria.obtener_historial()
        print(f"\n📊 Progreso después de {len(historial)} iteraciones:")

        for i, h in enumerate(historial[-6:], max(1, len(historial)-5)):
            puntaje = h['metricas']['puntaje_global'] * 100
            diagnostico = h.get('diagnostico', '')
            print(f"   {i}. Puntaje: {puntaje:.1f}% - {diagnostico[:50]}")

        mejor_puntaje = self.memoria.obtener_mejor_puntaje() * 100
        print(f"\n🏆 Mejor puntaje hasta ahora: {mejor_puntaje:.1f}%")

        ultimo = historial[-1] if historial else None
        if ultimo and ultimo.get('codigo'):
            codigo_resumido = ultimo['codigo'][:200] + "..." if len(ultimo['codigo']) > 200 else ultimo['codigo']
            print("\n📝 Último código generado:")
            print("-" * 40)
            print(codigo_resumido)
            print("-" * 40)

        print("\n🔮 ¿Qué quieres hacer?")
        print("  1. Continuar automáticamente")
        print("  2. Cambiar el rumbo (dar feedback específico)")
        print("  3. Extender iteraciones (aumentar límite)")
        print("  4. Detener y guardar resultados")
        print("  5. Ver el código completo de la última iteración")
        print("  6. Cambiar objetivo (editar descripción)")

        while True:
            try:
                opcion = input("\n👉 Elige una opción (1-6): ").strip()

                if opcion == "1":
                    print("\n✅ Continuando automáticamente...")
                    break

                elif opcion == "2":
                    print("\n💬 Escribe tu feedback para guiar al agente:")
                    self.feedback_usuario = input("👉 Feedback: ").strip()
                    print(f"\n✅ Feedback guardado: '{self.feedback_usuario}'")
                    break

                elif opcion == "3":
                    print(f"\n🔢 Iteraciones actuales: {self.max_iteraciones}")
                    try:
                        extra = int(input("👉 ¿Cuántas iteraciones más quieres añadir? "))
                        if extra > 0:
                            self.max_iteraciones += extra
                            self._actualizar_config(max_iteraciones=self.max_iteraciones)
                            print(f"✅ Límite extendido a {self.max_iteraciones} iteraciones")
                        else:
                            print("❌ Debe ser un número positivo")
                    except ValueError:
                        print("❌ Ingresa un número válido")
                    break

                elif opcion == "4":
                    print("\n⏹️  Deteniendo ejecución...")
                    self.max_iteraciones = self.iteracion
                    self.interaccion_activa = False
                    break

                elif opcion == "5":
                    if ultimo and ultimo.get('codigo'):
                        print("\n📝 CÓDIGO COMPLETO DE LA ÚLTIMA ITERACIÓN:")
                        print("="*50)
                        print(ultimo['codigo'])
                        print("="*50)
                    else:
                        print("❌ No hay código para mostrar")
                    continue

                elif opcion == "6":
                    print("\n📝 Edita la descripción del objetivo:")
                    print(f"   Actual: {self.config['descripcion']}")
                    nueva_desc = input("👉 Nueva descripción: ").strip()
                    if nueva_desc:
                        self._actualizar_config(descripcion=nueva_desc)
                        print(f"✅ Objetivo actualizado: '{nueva_desc}'")
                    break

                else:
                    print("❌ Opción inválida. Elige 1-6")

            except KeyboardInterrupt:
                print("\n\n⚠️  Interrumpido por el usuario")
                self.max_iteraciones = self.iteracion
                self.interaccion_activa = False
                break

        print("-"*50)

    def limpiar(self):
        self.ejecutor.limpiar()
