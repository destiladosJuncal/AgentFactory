import subprocess
import tempfile
import os
import time
import importlib.util
from typing import Dict, Any, List

from core import interprete


MAX_TRACEBACK_CHARS = 1_500


def resumen_de_traceback(stderr: str) -> str:
    """Se queda con la parte útil de un traceback para mandársela al modelo.

    Un traceback largo es casi todo ruido de frames internos; lo que sirve para
    corregir el código es la excepción final y las últimas llamadas. Si viene
    corto, se manda entero.
    """
    stderr = (stderr or "").strip()
    if not stderr:
        return "El proceso terminó con error pero no escribió nada en stderr"
    if len(stderr) <= MAX_TRACEBACK_CHARS:
        return stderr

    lineas = stderr.splitlines()
    # La última línea sin sangría es el tipo de excepción y su mensaje.
    excepcion = next((l for l in reversed(lineas) if l and not l.startswith((' ', '\t'))),
                     lineas[-1])
    cola = "\n".join(lineas[-12:])
    return f"{excepcion}\n\n(últimas líneas del traceback)\n{cola}"[:MAX_TRACEBACK_CHARS]


class Ejecutor:
    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="sandbox_")

    def ejecutar(self, codigo: str, tests: List = None) -> Dict[str, Any]:
        archivo = os.path.join(self.temp_dir, "solucion.py")
        with open(archivo, 'w', encoding='utf-8') as f:
            f.write(codigo)

        if tests:
            return self._ejecutar_con_tests(archivo, tests)
        return self._ejecutar_simple(archivo)

    def _ejecutar_con_tests(self, archivo: str, tests: List) -> Dict:
        resultados = {
            'estado': 'exito',
            'tests_pasados': 0,
            'tests_fallidos': [],
            'errores': [],
            'tiempo_ms': 0,
            'memoria_mb': 0,
            'tests_totales': len(tests)
        }

        try:
            spec = importlib.util.spec_from_file_location("solucion", archivo)
            if spec is None:
                raise ImportError(f"No se pudo cargar el módulo desde {archivo}")

            modulo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modulo)

            for test in tests:
                try:
                    if not hasattr(modulo, test['funcion']):
                        resultados['tests_fallidos'].append({
                            'test': test['nombre'],
                            'error': f"Función '{test['funcion']}' no encontrada"
                        })
                        continue

                    inicio = time.time()
                    func = getattr(modulo, test['funcion'])
                    resultado = func(*test['args'])
                    fin = time.time()
                    resultados['tiempo_ms'] += (fin - inicio) * 1000

                    if resultado == test['esperado']:
                        resultados['tests_pasados'] += 1
                    else:
                        resultados['tests_fallidos'].append({
                            'test': test['nombre'],
                            'esperado': test['esperado'],
                            'obtenido': resultado
                        })

                except Exception as e:
                    resultados['tests_fallidos'].append({
                        'test': test['nombre'],
                        'error': str(e)
                    })
                    resultados['errores'].append(str(e))

            try:
                import psutil
                proceso = psutil.Process()
                resultados['memoria_mb'] = proceso.memory_info().rss / (1024 * 1024)
            except Exception:
                resultados['memoria_mb'] = 0

            resultados['estado'] = 'exito' if len(resultados['tests_fallidos']) == 0 else 'fallo'

        except Exception as e:
            resultados['estado'] = 'error'
            resultados['errores'].append(str(e))
            resultados['tests_fallidos'].append({
                'test': 'importacion',
                'error': str(e)
            })

        return resultados

    def _ejecutar_simple(self, archivo: str) -> Dict:
        try:
            resultado = subprocess.run(
                [interprete.interprete(), archivo],
                capture_output=True,
                text=True,
                timeout=5
            )
            salida = {
                'estado': 'exito' if resultado.returncode == 0 else 'error',
                'stdout': resultado.stdout,
                'stderr': resultado.stderr,
                'codigo_retorno': resultado.returncode
            }
            if resultado.returncode != 0:
                # SIN ESTO el Evaluador arma el diagnóstico con
                # resultado.get('error', '') y le llega vacío: el traceback se
                # capturaba y se tiraba, así que el modelo reintentaba sin
                # enterarse de qué había fallado.
                salida['error'] = resumen_de_traceback(resultado.stderr)
            return salida
        except subprocess.TimeoutExpired:
            return {'estado': 'timeout', 'error': 'Tiempo de ejecución excedido'}
        except Exception as e:
            return {'estado': 'error', 'error': str(e)}

    def limpiar(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
