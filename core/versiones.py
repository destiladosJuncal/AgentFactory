"""
Historial de versiones del CÓDIGO instalado del agente, para poder volver atrás
si un update rompe algo (⌘0 en la UI).

Qué versiona: los archivos .py de la instalación —core/, los main_*.py, agente.py
y el main_ui.py que vive adentro del .app—.

Qué NO versiona, a propósito: tus datos. Proyectos, conversaciones y biblioteca
viven en $HOME/tmp/agent_code/ y no se tocan nunca al restaurar. Volver a una
versión anterior del código jamás te hace perder una conversación.

Los snapshots quedan en $HOME/tmp/agent_code/_versiones/<timestamp>/, que es
tuyo (no hace falta sudo) y sigue la convención de _biblioteca/_conversaciones.

La captura es automática: cada vez que la UI arranca compara el código actual
contra el último snapshot y, si cambió, guarda uno nuevo. Así cualquier update
—mío, tuyo o de un instalador— queda registrado sin que nadie se acuerde de
hacerlo.
"""

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.proyectos import AGENT_CODE_DIR

VERSIONES_DIR = AGENT_CODE_DIR / "_versiones"

# Cuántos snapshots conservar. Son unos pocos KB cada uno (solo texto .py).
MAX_SNAPSHOTS = 20


def _hash_archivo(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()[:16]


class GestorVersiones:
    def __init__(self, install_dir: Path, app_resources: Optional[Path] = None,
                 base_dir: Optional[Path] = None):
        self.install_dir = Path(install_dir)
        self.app_resources = Path(app_resources) if app_resources else None
        self.base_dir = Path(base_dir) if base_dir else VERSIONES_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # -- qué archivos entran -------------------------------------------------

    def _archivos_rastreados(self) -> List[Path]:
        """Los .py de la instalación, más el main_ui.py de adentro del .app."""
        archivos: List[Path] = []
        if self.install_dir.is_dir():
            archivos += sorted(self.install_dir.glob("*.py"))
            archivos += sorted((self.install_dir / "core").glob("*.py"))
        if self.app_resources and (self.app_resources / "main_ui.py").exists():
            archivos.append(self.app_resources / "main_ui.py")
        return archivos

    def _clave_relativa(self, ruta: Path) -> str:
        """Ruta que identifica al archivo dentro del snapshot. El main_ui.py del
        .app se guarda aparte para no chocar con uno homónimo de la instalación."""
        if self.app_resources and ruta.parent == self.app_resources:
            return f"_app/{ruta.name}"
        try:
            return str(ruta.relative_to(self.install_dir))
        except ValueError:
            return ruta.name

    def _huella_actual(self) -> Dict[str, str]:
        huella = {}
        for archivo in self._archivos_rastreados():
            try:
                huella[self._clave_relativa(archivo)] = _hash_archivo(archivo)
            except OSError:
                continue
        return huella

    # -- snapshots -----------------------------------------------------------

    def listar(self) -> List[Dict[str, Any]]:
        """Snapshots del más nuevo al más viejo."""
        snapshots = []
        for d in self.base_dir.iterdir():
            manifiesto = d / "manifiesto.json"
            if not d.is_dir() or not manifiesto.exists():
                continue
            try:
                datos = json.loads(manifiesto.read_text(encoding='utf-8'))
            except Exception:
                continue
            datos["id"] = d.name
            datos["path"] = d
            snapshots.append(datos)
        return sorted(snapshots, key=lambda s: s.get("creado", ""), reverse=True)

    def ultimo(self) -> Optional[Dict[str, Any]]:
        snapshots = self.listar()
        return snapshots[0] if snapshots else None

    def crear_snapshot(self, etiqueta: str = "") -> Dict[str, Any]:
        archivos = self._archivos_rastreados()
        if not archivos:
            return {"error": f"No encontré archivos para versionar en {self.install_dir}. "
                             f"¿Está instalado el agente?"}

        marca = time.strftime("%Y%m%d-%H%M%S")
        destino = self.base_dir / marca
        i = 2
        while destino.exists():
            destino = self.base_dir / f"{marca}-{i}"
            i += 1

        (destino / "archivos").mkdir(parents=True)
        huella = {}
        for archivo in archivos:
            clave = self._clave_relativa(archivo)
            copia = destino / "archivos" / clave
            copia.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(archivo, copia)
                huella[clave] = _hash_archivo(archivo)
            except OSError:
                continue

        manifiesto = {
            "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "etiqueta": etiqueta or "sin etiqueta",
            "archivos": len(huella),
            "huella": huella,
        }
        (destino / "manifiesto.json").write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding='utf-8'
        )

        self._podar()
        manifiesto["id"] = destino.name
        return manifiesto

    def snapshot_si_cambio(self, etiqueta: str = "") -> Optional[Dict[str, Any]]:
        """Guarda un snapshot solo si el código cambió desde el último. Es lo que
        llama la UI al arrancar: barato y sin duplicados."""
        actual = self._huella_actual()
        if not actual:
            return None
        ultimo = self.ultimo()
        if ultimo and ultimo.get("huella") == actual:
            return None
        return self.crear_snapshot(etiqueta)

    def _podar(self):
        sobrantes = self.listar()[MAX_SNAPSHOTS:]
        for s in sobrantes:
            shutil.rmtree(s["path"], ignore_errors=True)

    # -- restaurar -----------------------------------------------------------

    def diferencias(self, id_snapshot: str) -> Dict[str, Any]:
        """Qué archivos cambiarían al restaurar este snapshot."""
        snapshot = next((s for s in self.listar() if s["id"] == id_snapshot), None)
        if not snapshot:
            return {"error": f"No existe la versión '{id_snapshot}'"}

        actual = self._huella_actual()
        guardada = snapshot.get("huella", {})
        return {
            "modificados": sorted(k for k in guardada if k in actual and actual[k] != guardada[k]),
            "restaurados": sorted(k for k in guardada if k not in actual),
            "sin_cambios": sorted(k for k in guardada if actual.get(k) == guardada[k]),
            "nuevos_no_incluidos": sorted(k for k in actual if k not in guardada),
        }

    def restaurar(self, id_snapshot: str) -> Dict[str, Any]:
        """Devuelve el código a como estaba en ese snapshot.

        Antes de tocar nada guarda un snapshot del estado actual, así restaurar
        también se puede deshacer: nunca perdés el punto del que venías.
        """
        snapshot = next((s for s in self.listar() if s["id"] == id_snapshot), None)
        if not snapshot:
            return {"error": f"No existe la versión '{id_snapshot}'"}

        origen = snapshot["path"] / "archivos"
        if not origen.is_dir():
            return {"error": f"La versión '{id_snapshot}' no tiene archivos guardados"}

        self.crear_snapshot(f"antes de restaurar {id_snapshot}")

        restaurados, fallidos = [], []
        for clave in snapshot.get("huella", {}):
            copia = origen / clave
            if not copia.exists():
                continue
            if clave.startswith("_app/"):
                if not self.app_resources:
                    fallidos.append((clave, "no sé dónde está el .app"))
                    continue
                destino = self.app_resources / clave[len("_app/"):]
            else:
                destino = self.install_dir / clave
            try:
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copia, destino)
                restaurados.append(clave)
            except OSError as e:
                fallidos.append((clave, str(e)))

        resultado: Dict[str, Any] = {
            "restaurados": restaurados,
            "version": id_snapshot,
            "etiqueta": snapshot.get("etiqueta", ""),
        }
        if fallidos:
            resultado["fallidos"] = fallidos
            resultado["error_permisos"] = any(
                "Permission denied" in motivo for _, motivo in fallidos
            )
        return resultado

    def borrar(self, id_snapshot: str) -> Dict[str, Any]:
        snapshot = next((s for s in self.listar() if s["id"] == id_snapshot), None)
        if not snapshot:
            return {"error": f"No existe la versión '{id_snapshot}'"}
        shutil.rmtree(snapshot["path"], ignore_errors=True)
        return {"borrado": id_snapshot}
