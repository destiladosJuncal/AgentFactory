from typing import List, Dict, Any


class Memoria:
    def __init__(self):
        self.historial = []

    def guardar_iteracion(self, datos: Dict):
        self.historial.append(datos)

    def obtener_historial(self) -> List:
        return self.historial

    def obtener_ultimo_codigo(self) -> str:
        if self.historial:
            return self.historial[-1].get('codigo', '')
        return ''

    def obtener_mejor_puntaje(self) -> float:
        if not self.historial:
            return 0.0
        mejores = [h.get('metricas', {}).get('puntaje_global', 0) for h in self.historial]
        return max(mejores) if mejores else 0.0

    def obtener_diagnosticos(self) -> List:
        return [h.get('diagnostico', '') for h in self.historial if h.get('diagnostico')]
