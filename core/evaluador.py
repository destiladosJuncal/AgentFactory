import re
from typing import Dict, Any


class Evaluador:
    def evaluar(self, codigo: str, resultado: Dict, objetivos: Dict) -> Dict:
        metricas = {
            'puntaje_funcionalidad': 0,
            'puntaje_eficiencia': 0,
            'puntaje_calidad': 0,
            'puntaje_global': 0,
            'diagnostico': '',
            'razon': ''
        }

        if 'tests_pasados' in resultado:
            tests_totales = len(resultado.get('tests_fallidos', [])) + resultado['tests_pasados']
            if tests_totales > 0:
                metricas['puntaje_funcionalidad'] = resultado['tests_pasados'] / tests_totales
                metricas['tests_pasados'] = resultado['tests_pasados']
                metricas['tests_totales'] = tests_totales

                if resultado.get('tests_fallidos'):
                    fallos = resultado['tests_fallidos']
                    metricas['razon'] = f"Fallaron {len(fallos)} tests"
                    # Con el nombre del test solo, el modelo no sabe qué corregir.
                    # Le pasamos qué esperaba y qué obtuvo.
                    detalles = []
                    for t in fallos[:8]:
                        nombre = t.get('test', '?')
                        if 'error' in t:
                            detalles.append(f"- {nombre}: {t['error']}")
                        else:
                            detalles.append(
                                f"- {nombre}: esperaba {t.get('esperado')!r}, "
                                f"obtuvo {t.get('obtenido')!r}")
                    metricas['diagnostico'] = "Tests fallidos:\n" + "\n".join(detalles)
                else:
                    metricas['razon'] = "Todos los tests pasaron"
                    metricas['diagnostico'] = "Funcionalidad correcta"
            else:
                metricas['puntaje_funcionalidad'] = 0
                metricas['razon'] = "No se ejecutaron tests"
        else:
            metricas['puntaje_funcionalidad'] = 1.0 if resultado.get('estado') == 'exito' else 0

        if 'tiempo_ms' in resultado:
            max_tiempo = objetivos.get('eficiencia', {}).get('tiempo_maximo_ms', 1000)
            if max_tiempo > 0:
                metricas['puntaje_eficiencia'] = max(0, min(1, 1 - (resultado['tiempo_ms'] / max_tiempo)))

                if resultado['tiempo_ms'] > max_tiempo:
                    metricas['razon'] += f" Tiempo excedido: {resultado['tiempo_ms']:.0f}ms > {max_tiempo}ms"

        if 'memoria_mb' in resultado:
            max_memoria = objetivos.get('eficiencia', {}).get('memoria_maxima_mb', 100)
            if max_memoria > 0:
                puntaje_memoria = max(0, min(1, 1 - (resultado['memoria_mb'] / max_memoria)))
                metricas['puntaje_eficiencia'] = min(
                    metricas.get('puntaje_eficiencia', 1),
                    puntaje_memoria
                )

        if 'puntaje_eficiencia' not in metricas or metricas['puntaje_eficiencia'] == 0:
            metricas['puntaje_eficiencia'] = 0.8

        metricas['puntaje_calidad'] = self._evaluar_calidad_codigo(codigo)

        pesos = {'funcionalidad': 0.6, 'eficiencia': 0.2, 'calidad': 0.2}
        metricas['puntaje_global'] = (
            metricas['puntaje_funcionalidad'] * pesos['funcionalidad'] +
            metricas['puntaje_eficiencia'] * pesos['eficiencia'] +
            metricas['puntaje_calidad'] * pesos['calidad']
        )
        metricas['puntaje_global'] = max(0, min(1, metricas['puntaje_global']))

        if resultado.get('estado') in ['error', 'timeout']:
            metricas['puntaje_global'] *= 0.5
            # El detalle sale de 'error' y, si no está, del stderr crudo. Antes
            # era solo resultado.get('error',''), que en un fallo por traceback
            # no existe: el diagnóstico quedaba en "Error en ejecución: " y el
            # modelo reintentaba a ciegas.
            detalle = (resultado.get('error')
                       or (resultado.get('stderr') or '').strip()
                       or f"terminó con código {resultado.get('codigo_retorno', '?')}")
            metricas['razon'] = f"Error de ejecución: {detalle}"
            metricas['diagnostico'] = f"Error en ejecución:\n{detalle}"

        return metricas

    def _evaluar_calidad_codigo(self, codigo: str) -> float:
        puntaje = 1.0

        if '"""' not in codigo and "'''" not in codigo:
            puntaje -= 0.15

        lineas = codigo.split('\n')
        comentarios = sum(1 for l in lineas if l.strip().startswith('#'))
        if comentarios == 0:
            puntaje -= 0.1

        nombres_malos = re.findall(r'\b[a-z]\b', codigo)
        if len(nombres_malos) > 3:
            puntaje -= 0.1

        for i, linea in enumerate(lineas):
            if 'def ' in linea and not linea.strip().startswith('#'):
                fin = i + 1
                while fin < len(lineas) and not lineas[fin].strip().startswith('def '):
                    fin += 1
                largo = fin - i
                if largo > 50:
                    puntaje -= 0.05

        return max(0, min(1, puntaje))

    def objetivos_cumplidos(self, metricas: Dict, umbral: float) -> bool:
        return metricas.get('puntaje_global', 0) >= umbral
