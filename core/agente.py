import json
import sys
from typing import Dict, Any
from core.planificador import Planificador
from core.generador import Generador
from core.ejecutor import Ejecutor
from core.evaluador import Evaluador
from core.memoria import Memory


class AgenteCodigo:
    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        try:
            with open('tests/casos_prueba.json', 'r', encoding='utf-8') as f:
                self.tests = json.load(f)
        except Exception:
            self.tests = []
            print("⚠️  No se encontraron tests")

        self.planificador = Planificador()
        self.generador = Generador()
        self.ejecutor = Ejecutor()
        self.evaluador = Evaluador()
        self.memoria = Memory()
        self.objetivos_alcanzados = False
        self.iteracion = 0

    def ejecutar(self) -> Dict[str, Any]:
        print(f"\n📋 Objetivo: {self.config['descripcion']}")
        print(f"🎯 Umbral de éxito: {self.config['umbral_global']*100:.0f}%")
        print(f"🔄 Máximo de iteraciones: {self.config['max_iteraciones']}")

        while not self.objetivos_alcanzados and self.iteracion < self.config['max_iteraciones']:
            self.iteracion += 1
            print(f"\n{'='*50}")
            print(f"🔄 Iteración {self.iteracion}/{self.config['max_iteraciones']}")
            print(f"{'='*50}")

            plan = self.planificador.crear_plan(
                self.config['objetivos'],
                self.memoria.get_history()
            )
            print(f"📋 Plan: {plan['estrategia']}")

            codigo = self.generador.generar(
                plan=plan,
                historial=self.memoria.get_history(),
                descripcion=self.config['descripcion']
            )
            print(f"💻 Código generado ({len(codigo)} caracteres)")

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

            self.memoria.save_iteration({
                'iteracion': self.iteracion,
                'codigo': codigo,
                'resultado': resultado,
                'metricas': metricas,
                'diagnostico': metricas.get('diagnostico', ''),
                'razon': metricas.get('razon', '')
            })

            self.objetivos_alcanzados = self.evaluador.objetivos_cumplidos(
                metricas,
                self.config['umbral_global']
            )

            if not self.objetivos_alcanzados:
                print(f"❌ Objetivo no alcanzado: {metricas.get('razon', '')}")
                self.generador.actualizar_contexto(metricas.get('diagnostico', ''))

        return {
            'exito': self.objetivos_alcanzados,
            'iteraciones': self.iteracion,
            'codigo_final': self.memoria.last_code() if self.objetivos_alcanzados else None,
            'historial': self.memoria.get_history(),
            'mejor_puntaje': self.memoria.best_score()
        }

    def limpiar(self):
        self.ejecutor.limpiar()
