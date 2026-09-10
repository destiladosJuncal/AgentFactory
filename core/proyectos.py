"""
Gestor de proyectos persistentes del agente.

Cada proyecto vive en su propia carpeta dentro de $HOME/tmp/agent_code/:

  $HOME/tmp/agent_code/
    mi-proyecto/
      config.json         # descripción, objetivos, umbral, max_iteraciones, estado
      memoria.json         # historial completo de iteraciones (métricas, diagnóstico)
      iteraciones/
        iter_001.py
        iter_002.py
        ...
      solucion.py           # se crea cuando el proyecto alcanza el objetivo
      ultima_version.py     # último código generado, si nunca se alcanzó el objetivo
      (el agente puede crear más archivos/carpetas acá vía sus herramientas)
"""

import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

# La resolución vive en core/rutas.py; se reexporta con el nombre de siempre
# para no tocar los módulos que ya lo importan de acá.
from core.rutas import dir_datos  # noqa: E402

AGENT_CODE_DIR = dir_datos()


def _slugify(texto: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', texto.strip().lower()).strip('-')
    return slug[:40] or "proyecto"


class GestorProyectos:
    def __init__(self, base_dir: Path = None):
        self.base_dir = Path(base_dir) if base_dir else AGENT_CODE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def listar_proyectos(self) -> List[Dict[str, Any]]:
        proyectos = []
        for d in sorted(self.base_dir.iterdir()):
            if not d.is_dir():
                continue
            config_path = d / "config.json"
            if not config_path.exists():
                continue
            try:
                config = json.loads(config_path.read_text(encoding='utf-8'))
            except Exception:
                continue

            n_iter = 0
            mejor = 0.0
            memoria_path = d / "memoria.json"
            if memoria_path.exists():
                try:
                    historial = json.loads(memoria_path.read_text(encoding='utf-8'))
                    n_iter = len(historial)
                    puntajes = [h.get('metricas', {}).get('puntaje_global', 0) for h in historial]
                    mejor = max(puntajes) if puntajes else 0.0
                except Exception:
                    pass

            proyectos.append({
                "nombre": d.name,
                "descripcion": config.get("descripcion", ""),
                "creado": config.get("creado", ""),
                "actualizado": config.get("actualizado", config.get("creado", "")),
                "iteraciones": n_iter,
                "mejor_puntaje": mejor,
                "exito": bool(config.get("exito", False)),
                "path": d
            })

        return sorted(proyectos, key=lambda p: p.get("actualizado", ""), reverse=True)

    def crear_proyecto(self, descripcion: str, objetivos: Dict, umbral_global: float,
                        max_iteraciones: int) -> Path:
        slug_base = _slugify(descripcion)
        slug = slug_base
        i = 2
        while (self.base_dir / slug).exists():
            slug = f"{slug_base}-{i}"
            i += 1

        proyecto_dir = self.base_dir / slug
        (proyecto_dir / "iteraciones").mkdir(parents=True, exist_ok=True)

        ahora = time.strftime("%Y-%m-%d %H:%M:%S")
        config = {
            "descripcion": descripcion,
            "objetivos": objetivos,
            "umbral_global": umbral_global,
            "max_iteraciones": max_iteraciones,
            "creado": ahora,
            "actualizado": ahora,
            "exito": False
        }
        (proyecto_dir / "config.json").write_text(
            json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        (proyecto_dir / "memoria.json").write_text("[]", encoding='utf-8')
        return proyecto_dir

    def cargar_proyecto(self, nombre: str) -> Optional[Path]:
        proyecto_dir = self.base_dir / nombre
        if proyecto_dir.exists() and (proyecto_dir / "config.json").exists():
            return proyecto_dir
        return None
