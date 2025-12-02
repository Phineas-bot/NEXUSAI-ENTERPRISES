from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import BinaryIO, Dict, Optional


class RealFileStore:
    """Lightweight disk-backed blob store for demo workloads."""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._index_path = self.base_path / "index.json"
        self._entries: Dict[str, Dict[str, object]] = {}
        self._load_index()

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._entries = data
        except (json.JSONDecodeError, OSError):
            # Corrupt index; start fresh but keep existing blobs.
            self._entries = {}

    def _persist_index(self) -> None:
        temp_path = self._index_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")
        temp_path.replace(self._index_path)

    def save_stream(self, stream: BinaryIO, original_name: Optional[str] = None) -> tuple[str, int]:
        suffix = ""
        if original_name:
            suffix = Path(original_name).suffix
        if not suffix:
            suffix = ".bin"
        blob_id = uuid.uuid4().hex
        target_path = self.base_path / f"{blob_id}{suffix}"
        stream.seek(0)
        size_bytes = 0
        with target_path.open("wb") as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                size_bytes += len(chunk)
        stream.seek(0)
        return str(target_path), size_bytes

    def register_dataset(self, dataset_id: str, path: str, original_name: str, size_bytes: int) -> None:
        self._entries[dataset_id] = {
            "path": path,
            "original_name": original_name,
            "size_bytes": int(size_bytes),
        }
        self._persist_index()

    def resolve(self, dataset_id: str) -> Optional[Dict[str, object]]:
        entry = self._entries.get(dataset_id)
        if not entry:
            return None
        if not os.path.exists(entry.get("path", "")):
            return None
        return entry

    def resolve_by_name(self, original_name: str) -> Optional[Dict[str, object]]:
        if not original_name:
            return None
        target = original_name.lower()
        for entry in self._entries.values():
            name = str(entry.get("original_name", "")).lower()
            if name == target and os.path.exists(entry.get("path", "")):
                return entry
        return None

    def remove(self, dataset_id: str) -> None:
        entry = self._entries.pop(dataset_id, None)
        if entry:
            try:
                os.remove(entry.get("path", ""))
            except OSError:
                pass
            self._persist_index()

    def cleanup_orphans(self) -> None:
        known_paths = {Path(entry.get("path", "")).resolve() for entry in self._entries.values() if entry.get("path")}
        for path in self.base_path.glob("*"):
            if path == self._index_path:
                continue
            if path.resolve() not in known_paths and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    continue
