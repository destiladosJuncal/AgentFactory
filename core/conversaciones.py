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

# Al archivar una conversación, su parte pesada (mensajes.json + workspace/) se
# empaqueta acá DENTRO de la propia carpeta, con xz/LZMA —bastante mejor que zip
# en texto JSON—, y se borran los originales. meta.json queda afuera, sin tocar,
# para que el listado siga andando sin descomprimir. 'archivada' y 'comprimida'
# van siempre juntas: archivar comprime, abrir/desarchivar restaura.
ARCHIVO_COMPRIMIDO = "_archivado.tar.xz"


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

            # Si está comprimida, mensajes.json no está en disco: uso el conteo
            # cacheado en meta al archivar, así no hace falta descomprimir para
            # listar.
            n_msgs = 0
            if meta.get("comprimida"):
                n_msgs = int(meta.get("mensajes_n", 0) or 0)
            else:
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
                "archivada": bool(meta.get("archivada", False)),
                "comprimida": bool(meta.get("comprimida", False)),
                "mensajes": n_msgs,
                "path": d
            })

        return sorted(conversaciones, key=lambda s: s.get("actualizado", ""), reverse=True)

    def marcar_archivada(self, nombre: str, valor: bool = True) -> bool:
        """Archiva/desarchiva una conversación: un flag en meta.json que la saca
        de (o la devuelve a) la lista principal. No toca el historial ni la
        carpeta, así que es reversible y no pierde nada."""
        d = self.cargar_conversacion(nombre)
        if d is None:
            return False
        meta_path = d / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            meta = {}
        meta["archivada"] = bool(valor)
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
        return True

    def _actualizar_meta(self, d: Path, cambios: Dict[str, Any]) -> None:
        """Mezcla `cambios` en meta.json (None borra la clave) sin pisar el resto."""
        meta_path = d / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            meta = {}
        for k, v in cambios.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')

    @staticmethod
    def _contar_mensajes(d: Path) -> int:
        p = d / "mensajes.json"
        if p.exists():
            try:
                return len(json.loads(p.read_text(encoding='utf-8')))
            except Exception:
                return 0
        return 0

    def esta_comprimida(self, nombre: str) -> bool:
        d = self.cargar_conversacion(nombre)
        return bool(d is not None and (d / ARCHIVO_COMPRIMIDO).exists())

    def comprimir_conversacion(self, nombre: str) -> bool:
        """Empaqueta mensajes.json + workspace/ en un .tar.xz DENTRO de la carpeta
        y borra los originales; deja meta.json con archivada=comprimida=True y el
        conteo de mensajes cacheado. Libera espacio pero es 100% reversible con
        descomprimir_conversacion. Un workspace apuntado afuera (ruta propia del
        usuario) NO se toca: solo se empaqueta el workspace por defecto."""
        import shutil
        import tarfile
        d = self.cargar_conversacion(nombre)
        if d is None:
            return False
        tar = d / ARCHIVO_COMPRIMIDO
        msgs = d / "mensajes.json"
        ws = d / "workspace"
        n = self._contar_mensajes(d)
        if not tar.exists():
            tmp = d / (ARCHIVO_COMPRIMIDO + ".tmp")
            try:
                with tarfile.open(tmp, "w:xz") as t:
                    if msgs.exists():
                        t.add(msgs, arcname="mensajes.json")
                    if ws.exists():
                        t.add(ws, arcname="workspace")
                tmp.replace(tar)            # publicación atómica: o está entero o no está
            except Exception:
                try:
                    tmp.unlink()
                except Exception:
                    pass
                return False
            # Recién con el .tar.xz escrito OK, borro los originales.
            if msgs.exists():
                try:
                    msgs.unlink()
                except Exception:
                    pass
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
        self._actualizar_meta(d, {"archivada": True, "comprimida": True, "mensajes_n": n})
        return True

    def descomprimir_conversacion(self, nombre: str) -> bool:
        """Restaura mensajes.json + workspace/ desde el .tar.xz y lo borra; deja
        archivada=comprimida=False. No-op (salvo limpiar el flag) si no estaba
        comprimida."""
        import tarfile
        d = self.cargar_conversacion(nombre)
        if d is None:
            return False
        tar = d / ARCHIVO_COMPRIMIDO
        if tar.exists():
            try:
                with tarfile.open(tar, "r:xz") as t:
                    try:
                        t.extractall(d, filter="data")   # py3.12+: evita rutas fuera de d
                    except TypeError:
                        t.extractall(d)
                tar.unlink()
            except Exception:
                return False
        self._actualizar_meta(d, {"archivada": False, "comprimida": False, "mensajes_n": None})
        return True

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
