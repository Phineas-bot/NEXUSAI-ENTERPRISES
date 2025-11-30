"""Metadata service scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone
import uuid

from ..config import CloudDriveConfig
from ..models import FileEntry, FileManifest
from ..telemetry import TelemetryCollector
from .base import BaseService


@dataclass
class MetadataService(BaseService):
    """In-memory placeholder for metadata operations."""

    _files: Dict[str, FileEntry] = None
    _manifests: Dict[str, FileManifest] = None

    def __post_init__(self) -> None:
        if self._files is None:
            self._files = {}
        if self._manifests is None:
            self._manifests = {}

    def create_folder(self, org_id: str, parent_id: Optional[str], name: str, created_by: str) -> FileEntry:
        file_id = str(uuid.uuid4())
        entry = FileEntry(
            id=file_id,
            org_id=org_id,
            parent_id=parent_id,
            name=name,
            mime_type="application/vnd.dir",
            size_bytes=0,
            checksum=None,
            is_folder=True,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self._files[file_id] = entry
        self.emit_event("folder_created", file_id=file_id, org_id=org_id)
        return entry

    def register_manifest(self, manifest: FileManifest) -> None:
        self._manifests[manifest.manifest_id] = manifest
        self.emit_event("manifest_registered", manifest_id=manifest.manifest_id)

    def upsert_manifest(self, manifest: FileManifest) -> None:
        self._manifests[manifest.manifest_id] = manifest
        self.emit_event("manifest_updated", manifest_id=manifest.manifest_id)

    def list_children(self, parent_id: Optional[str]) -> List[FileEntry]:
        return [f for f in self._files.values() if f.parent_id == parent_id]

    def get_manifest(self, file_id: str) -> Optional[FileManifest]:
        for manifest in self._manifests.values():
            if manifest.file_id == file_id:
                return manifest
        return None

    def list_manifests(self) -> List[FileManifest]:
        return list(self._manifests.values())

    def get_file(self, file_id: str) -> Optional[FileEntry]:
        return self._files.get(file_id)
