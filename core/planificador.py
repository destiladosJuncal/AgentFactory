from typing import List, Dict, Any


class Planificador:
    def crear_plan(self, objetivos: Dict, historial: List) -> Dict:
        if not historial:
            return {
                'estrategia': 'Implementación directa',
                'enfoque': 'Código funcional y legible',
                'prioridad': 'Funcionalidad'
            }

        ultima = historial[-1]
        metricas = ultima.get('metricas', {})
        diagnostico = ultima.get('diagnostico', '')

        if 'error sintáctico' in diagnostico.lower() or 'syntax' in diagnostico.lower():
            return {
                'estrategia': 'Corrección sintáctica',
                'enfoque': 'Revisar estructura del código',
                'prioridad': 'Compilación'
            }

        if 'tests fallaron' in diagnostico.lower() or 'failed' in diagnostico.lower():
            if metricas.get('puntaje_funcionalidad', 0) < 0.5:
                return {
                    'estrategia': 'Reestructurar lógica',
                    'enfoque': 'Nuevo algoritmo',
                    'prioridad': 'Funcionalidad'
                }
            else:
                return {
                    'estrategia': 'Corrección de casos borde',
                    'enfoque': 'Manejar casos especiales',
                    'prioridad': 'Cobertura de tests'
                }

        if metricas.get('puntaje_eficiencia', 1) < 0.5:
            return {
                'estrategia': 'Optimizar rendimiento',
                'enfoque': 'Mejorar complejidad algorítmica',
                'prioridad': 'Eficiencia'
            }

        if metricas.get('puntaje_calidad', 0) < 0.6:
            return {
                'estrategia': 'Mejorar calidad',
                'enfoque': 'Refactorizar y documentar',
                'prioridad': 'Legibilidad'
            }

        return {
            'estrategia': 'Ajuste fino',
            'enfoque': 'Optimizar detalles',
            'prioridad': 'Precisión'
        }
