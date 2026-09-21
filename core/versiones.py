"""
Version history of the agent's installed CODE, so you can roll back if an update
breaks something (⌘0 in the UI).

What it versions: the installation's .py files —core/, the main_*.py, agente.py
and the main_ui.py that lives inside the .app—.

What it does NOT version, on purpose: your data. Projects, conversations and the
library live in $HOME/tmp/agent_code/ and are never touched on restore. Going
back to an earlier version of the code never makes you lose a conversation.

Snapshots land in $HOME/tmp/agent_code/_versiones/<timestamp>/, which is yours
(no sudo needed) and follows the _biblioteca/_conversaciones convention.

Capture is automatic: every time the UI starts it compares the current code
against the last snapshot and, if it changed, saves a new one. That way any
update —mine, yours or an installer's— is recorded without anyone remembering to
do it.

(The on-disk layout —the _versiones dir, the manifiesto.json file and its keys,
the returned dict keys the UI reads— stays Spanish on purpose: it's an on-disk
contract for a later migration phase.)
"""

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.proyectos import AGENT_CODE_DIR

VERSIONS_DIR = AGENT_CODE_DIR / "_versiones"

# How many snapshots to keep. They're a few KB each (only .py text).
MAX_SNAPSHOTS = 20


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class VersionManager:
    def __init__(self, install_dir: Path, app_resources: Optional[Path] = None,
                 base_dir: Optional[Path] = None):
        self.install_dir = Path(install_dir)
        self.app_resources = Path(app_resources) if app_resources else None
        self.base_dir = Path(base_dir) if base_dir else VERSIONS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # -- which files are included --------------------------------------------

    def _tracked_files(self) -> List[Path]:
        """The installation's .py files, plus the .app's inner main_ui.py."""
        files: List[Path] = []
        if self.install_dir.is_dir():
            files += sorted(self.install_dir.glob("*.py"))
            files += sorted((self.install_dir / "core").glob("*.py"))
        if self.app_resources and (self.app_resources / "main_ui.py").exists():
            files.append(self.app_resources / "main_ui.py")
        return files

    def _relative_key(self, path: Path) -> str:
        """Path that identifies the file inside the snapshot. The .app's
        main_ui.py is stored apart so it doesn't clash with a same-named one from
        the installation."""
        if self.app_resources and path.parent == self.app_resources:
            return f"_app/{path.name}"
        try:
            return str(path.relative_to(self.install_dir))
        except ValueError:
            return path.name

    def _current_fingerprint(self) -> Dict[str, str]:
        fingerprint = {}
        for file in self._tracked_files():
            try:
                fingerprint[self._relative_key(file)] = _hash_file(file)
            except OSError:
                continue
        return fingerprint

    # -- snapshots -----------------------------------------------------------

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """Snapshots from newest to oldest."""
        snapshots = []
        for d in self.base_dir.iterdir():
            manifest = d / "manifiesto.json"
            if not d.is_dir() or not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_text(encoding='utf-8'))
            except Exception:
                continue
            data["id"] = d.name
            data["path"] = d
            snapshots.append(data)
        return sorted(snapshots, key=lambda s: s.get("creado", ""), reverse=True)

    def latest(self) -> Optional[Dict[str, Any]]:
        snapshots = self.list_snapshots()
        return snapshots[0] if snapshots else None

    def create_snapshot(self, etiqueta: str = "") -> Dict[str, Any]:
        files = self._tracked_files()
        if not files:
            return {"error": f"No encontré archivos para versionar en {self.install_dir}. "
                             f"¿Está instalado el agente?"}

        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = self.base_dir / stamp
        i = 2
        while destination.exists():
            destination = self.base_dir / f"{stamp}-{i}"
            i += 1

        (destination / "archivos").mkdir(parents=True)
        fingerprint = {}
        for file in files:
            key = self._relative_key(file)
            copy = destination / "archivos" / key
            copy.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(file, copy)
                fingerprint[key] = _hash_file(file)
            except OSError:
                continue

        manifest = {
            "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "etiqueta": etiqueta or "sin etiqueta",
            "archivos": len(fingerprint),
            "huella": fingerprint,
        }
        (destination / "manifiesto.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8'
        )

        self._prune()
        manifest["id"] = destination.name
        return manifest

    def snapshot_if_changed(self, etiqueta: str = "") -> Optional[Dict[str, Any]]:
        """Saves a snapshot only if the code changed since the last one. It's
        what the UI calls at startup: cheap and without duplicates."""
        current = self._current_fingerprint()
        if not current:
            return None
        last = self.latest()
        if last and last.get("huella") == current:
            return None
        return self.create_snapshot(etiqueta)

    def _prune(self):
        extra = self.list_snapshots()[MAX_SNAPSHOTS:]
        for s in extra:
            shutil.rmtree(s["path"], ignore_errors=True)

    # -- restore -------------------------------------------------------------

    def diff(self, id_snapshot: str) -> Dict[str, Any]:
        """Which files would change on restoring this snapshot."""
        snapshot = next((s for s in self.list_snapshots() if s["id"] == id_snapshot), None)
        if not snapshot:
            return {"error": f"No existe la versión '{id_snapshot}'"}

        current = self._current_fingerprint()
        saved = snapshot.get("huella", {})
        return {
            "modificados": sorted(k for k in saved if k in current and current[k] != saved[k]),
            "restaurados": sorted(k for k in saved if k not in current),
            "sin_cambios": sorted(k for k in saved if current.get(k) == saved[k]),
            "nuevos_no_incluidos": sorted(k for k in current if k not in saved),
        }

    def restore(self, id_snapshot: str) -> Dict[str, Any]:
        """Returns the code to how it was in that snapshot.

        Before touching anything it saves a snapshot of the current state, so
        restoring can also be undone: you never lose the point you came from.
        """
        snapshot = next((s for s in self.list_snapshots() if s["id"] == id_snapshot), None)
        if not snapshot:
            return {"error": f"No existe la versión '{id_snapshot}'"}

        source = snapshot["path"] / "archivos"
        if not source.is_dir():
            return {"error": f"La versión '{id_snapshot}' no tiene archivos guardados"}

        self.create_snapshot(f"antes de restaurar {id_snapshot}")

        restored, failed = [], []
        for key in snapshot.get("huella", {}):
            copy = source / key
            if not copy.exists():
                continue
            if key.startswith("_app/"):
                if not self.app_resources:
                    failed.append((key, "no sé dónde está el .app"))
                    continue
                destination = self.app_resources / key[len("_app/"):]
            else:
                destination = self.install_dir / key
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copy, destination)
                restored.append(key)
            except OSError as e:
                failed.append((key, str(e)))

        result: Dict[str, Any] = {
            "restaurados": restored,
            "version": id_snapshot,
            "etiqueta": snapshot.get("etiqueta", ""),
        }
        if failed:
            result["fallidos"] = failed
            result["error_permisos"] = any(
                "Permission denied" in reason for _, reason in failed
            )
        return result

    def delete(self, id_snapshot: str) -> Dict[str, Any]:
        snapshot = next((s for s in self.list_snapshots() if s["id"] == id_snapshot), None)
        if not snapshot:
            return {"error": f"No existe la versión '{id_snapshot}'"}
        shutil.rmtree(snapshot["path"], ignore_errors=True)
        return {"borrado": id_snapshot}
