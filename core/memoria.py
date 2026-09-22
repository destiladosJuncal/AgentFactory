from typing import List, Dict, Any


class Memory:
    """In-memory history of the iterative loop's attempts.

    (The stored per-iteration dict keys —'codigo', 'metricas'/'puntaje_global',
    'diagnostico'— stay Spanish on purpose: they flow into the project history on
    disk, so renaming them belongs to the data-migration phase.)"""

    def __init__(self):
        self.history = []

    def save_iteration(self, data: Dict):
        self.history.append(data)

    def get_history(self) -> List:
        return self.history

    def last_code(self) -> str:
        if self.history:
            return self.history[-1].get('codigo', '')
        return ''

    def best_score(self) -> float:
        if not self.history:
            return 0.0
        best = [h.get('metricas', {}).get('puntaje_global', 0) for h in self.history]
        return max(best) if best else 0.0

    def diagnostics(self) -> List:
        return [h.get('diagnostico', '') for h in self.history if h.get('diagnostico')]
