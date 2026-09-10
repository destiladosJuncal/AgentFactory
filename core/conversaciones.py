"""
Gestor de conversaciones (chat) persistentes.

Cada conversación vive en $HOME/tmp/agent_code/_conversaciones/<conversacion>/:

  _conversaciones/
    mi-conversacion/
      meta.json          # título, creado, actualizado
      mensajes.json       # historial completo de la conversación
      workspace/           # sandbox de archivos de ESTA conversación (para las
                            # herramientas de lectura/escritura del agente)

Las conversaciones son para EJECUTAR sobre herramientas ya construidas
(propias del workspace o publicadas en la biblioteca compartida) — a
diferencia de los proyectos (core/proyectos.py), que son para CONSTRUIR
herramientas nuevas vía el modo iterativo.
"""

import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from core.proyectos import AGENT_CODE_DIR

CONVERSACIONES_DIR = AGENT_CODE_DIR / "_conversaciones"


def _slugify(texto: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', texto.strip().lower()).strip('-')
    return slug[:40] or "conversacion"


def _estimar_costo(uso: Dict[str, Any], meta: Dict[str, Any]) -> float:
    """Precio aproximado de una conversación vieja, a partir de sus tokens.

    Usa el modelo anotado en meta.json y, si no hay, el configurado hoy. Es una
    estimación: no guardamos con qué modelo corrió cada turno."""
    from core.proveedores import precio_de
    import os

    modelo = (meta.get("modelo")
              or os.getenv("DEEPSEEK_MODEL" if (meta.get("proveedor") or
                           os.getenv("AGENTE_PROVEEDOR", "deepseek")) == "deepseek"
                           else "ANTHROPIC_MODEL", ""))
    precio = precio_de(modelo)
    if not precio:
        return 0.0
    entrada, salida = precio
    return (int(uso.get("entrada", 0)) / 1e6) * entrada + \
           (int(uso.get("salida", 0)) / 1e6) * salida


class GestorConversaciones:
    def __init__(self, base_dir: Path = None):
        self.base_dir = Path(base_dir) if base_dir else CONVERSACIONES_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def listar_conversaciones(self) -> List[Dict[str, Any]]:
        conversaciones = []
        for d in sorted(self.base_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
            except Exception:
                continue

            n_msgs = 0
            mensajes_path = d / "mensajes.json"
            if mensajes_path.exists():
                try:
                    n_msgs = len(json.loads(mensajes_path.read_text(encoding='utf-8')))
                except Exception:
                    pass

            # El gasto acumulado se guarda en meta.json turno a turno.
            uso = meta.get("uso_total") or {}
            costo = float(uso.get("costo") or 0.0)
            completo = bool(uso.get("costo_conocido", True))
            estimado = False

            # 'costo_conocido=False' significa que hubo llamadas SIN precio
            # cargado, o sea que el acumulado guardado está incompleto: cubre
            # solo los turnos posteriores a que existieran los precios. En ese
            # caso el número guardado es peor que estimar desde los tokens
            # totales, que sí cubren toda la conversación.
            if (not completo or not costo) and (uso.get("entrada") or uso.get("salida")):
                estimacion = _estimar_costo(uso, meta)
                if estimacion > costo:
                    costo, estimado = estimacion, True

            conversaciones.append({
                "costo": costo,
                "costo_estimado": estimado,
                "costo_conocido": bool(uso.get("costo_conocido", True)),
                "tokens": int(uso.get("entrada", 0)) + int(uso.get("salida", 0)),
                "nombre": d.name,
                "titulo": meta.get("titulo", d.name),
                "creado": meta.get("creado", ""),
                "actualizado": meta.get("actualizado", meta.get("creado", "")),
                "mensajes": n_msgs,
                "path": d
            })

        return sorted(conversaciones, key=lambda s: s.get("actualizado", ""), reverse=True)

    def crear_conversacion(self, titulo: str = "") -> Path:
        slug_base = _slugify(titulo) if titulo else time.strftime("conversacion-%Y%m%d-%H%M%S")
        slug = slug_base
        i = 2
        while (self.base_dir / slug).exists():
            slug = f"{slug_base}-{i}"
            i += 1

        conversacion_dir = self.base_dir / slug
        (conversacion_dir / "workspace").mkdir(parents=True, exist_ok=True)

        ahora = time.strftime("%Y-%m-%d %H:%M:%S")
        meta = {"titulo": titulo or slug, "creado": ahora, "actualizado": ahora}
        (conversacion_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        (conversacion_dir / "mensajes.json").write_text("[]", encoding='utf-8')
        return conversacion_dir

    def cargar_conversacion(self, nombre: str) -> Optional[Path]:
        conversacion_dir = self.base_dir / nombre
        if conversacion_dir.exists() and (conversacion_dir / "meta.json").exists():
            return conversacion_dir
        return None
