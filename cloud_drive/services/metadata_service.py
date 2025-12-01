"""Metadata service scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
import uuid

from ..config import CloudDriveConfig
from ..models import FileEntry, FileManifest, FileVersion
from ..telemetry import TelemetryCollector
from .base import BaseService


@dataclass
class MetadataService(BaseService):
    """In-memory placeholder for metadata operations."""

    _files: Dict[str, FileEntry] = None
    _manifests: Dict[str, FileManifest] = None
    _current_manifests: Dict[str, str] = None
    _file_versions: Dict[str, List[FileVersion]] = None

    def __post_init__(self) -> None:
        if self._files is None:
            self._files = {}
        if self._manifests is None:
            self._manifests = {}
        if self._current_manifests is None:
            self._current_manifests = {}
        if self._file_versions is None:
            self._file_versions = {}

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
        self._current_manifests[manifest.file_id] = manifest.manifest_id
        self.emit_event("manifest_registered", manifest_id=manifest.manifest_id)

    def upsert_manifest(self, manifest: FileManifest) -> None:
        self._manifests[manifest.manifest_id] = manifest
        self._current_manifests[manifest.file_id] = manifest.manifest_id
        self.emit_event("manifest_updated", manifest_id=manifest.manifest_id)

    def list_children(self, parent_id: Optional[str]) -> List[FileEntry]:
        return [f for f in self._files.values() if f.parent_id == parent_id and f.deleted_at is None]

    def get_manifest(self, file_id: str) -> Optional[FileManifest]:
        manifest_id = self._current_manifests.get(file_id)
        if not manifest_id:
            return None
        return self._manifests.get(manifest_id)

    def list_manifests(self) -> List[FileManifest]:
        return list(self._manifests.values())

    def snapshot_stats(self) -> Dict[str, int]:
        return {
            "files": len(self._files),
            "manifests": len(self._manifests),
        }

    def get_file(self, file_id: str, *, include_deleted: bool = False) -> Optional[FileEntry]:
        entry = self._files.get(file_id)
        if entry is None:
            return None
        if entry.deleted_at and not include_deleted:
            return None
        return entry

    # Version-aware helpers -------------------------------------------------

    def ensure_file_entry(
        self,
        *,
        file_id: str,
        org_id: str,
        parent_id: Optional[str],
        name: str,
        mime_type: str,
        size_bytes: int,
        created_by: str,
        checksum: Optional[str] = None,
    ) -> FileEntry:
        entry = self._files.get(file_id)
        now = datetime.now(timezone.utc)
        if entry is None:
            entry = FileEntry(
                id=file_id,
                org_id=org_id,
                parent_id=parent_id,
                name=name,
                mime_type=mime_type,
                size_bytes=size_bytes,
                checksum=checksum,
                is_folder=False,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            self._files[file_id] = entry
            self.emit_event("file_created", file_id=file_id, org_id=org_id)
        else:
            entry.size_bytes = size_bytes
            entry.checksum = checksum
            entry.updated_at = now
            entry.name = name or entry.name
            entry.parent_id = parent_id or entry.parent_id
            entry.mime_type = mime_type or entry.mime_type
            entry.deleted_at = None
            entry.deleted_by = None
        return entry

    def record_version(
        self,
        *,
        file_id: str,
        manifest_id: str,
        size_bytes: int,
        actor: str,
        change_summary: Optional[str] = None,
        autosave: bool = False,
        is_pinned: bool = False,
        label: Optional[str] = None,
    ) -> FileVersion:
        versions = self._file_versions.setdefault(file_id, [])
        parent = versions[-1] if versions else None
        version = FileVersion(
            version_id=str(uuid.uuid4()),
            file_id=file_id,
            manifest_id=manifest_id,
            version_number=(parent.version_number + 1) if parent else 1,
            created_by=actor,
            created_at=datetime.now(timezone.utc),
            size_bytes=size_bytes,
            parent_version_id=parent.version_id if parent else None,
            change_summary=change_summary,
            autosave=autosave,
            is_pinned=is_pinned,
            label=label,
        )
        versions.append(version)
        self.emit_event("file_version_created", file_id=file_id, version_id=version.version_id)
        return version

    def list_versions(self, file_id: str) -> List[FileVersion]:
        versions = self._file_versions.get(file_id, [])
        return sorted(versions, key=lambda v: v.version_number, reverse=True)

    def get_version(self, file_id: str, version_id: str) -> Optional[FileVersion]:
        for version in self._file_versions.get(file_id, []):
            if version.version_id == version_id:
                return version
        return None

    def restore_version(self, file_id: str, version_id: str, actor: str) -> Optional[FileVersion]:
        target = self.get_version(file_id, version_id)
        if not target:
            return None
        self._current_manifests[file_id] = target.manifest_id
        restored = self.record_version(
            file_id=file_id,
            manifest_id=target.manifest_id,
            size_bytes=target.size_bytes,
            actor=actor,
            change_summary=f"restore:{version_id}",
        )
        self.emit_event("file_version_restored", file_id=file_id, version_id=version_id)
        return restored

    def update_version_metadata(
        self,
        file_id: str,
        version_id: str,
        *,
        label: Optional[str] = None,
        is_pinned: Optional[bool] = None,
        autosave: Optional[bool] = None,
        change_summary: Optional[str] = None,
    ) -> Optional[FileVersion]:
        version = self.get_version(file_id, version_id)
        if version is None:
            return None
        if label is not None:
            version.label = label
        if is_pinned is not None:
            version.is_pinned = is_pinned
        if autosave is not None:
            version.autosave = autosave
        if change_summary is not None:
            version.change_summary = change_summary
        self.emit_event("file_version_metadata_updated", file_id=file_id, version_id=version_id)
        return version

    # Trash lifecycle ---------------------------------------------------------

    def delete_file(self, file_id: str, actor: str) -> FileEntry:
        entry = self._files[file_id]
        if entry.deleted_at is None:
            entry.deleted_at = datetime.now(timezone.utc)
            entry.deleted_by = actor
            self.emit_event("file_trashed", file_id=file_id, actor=actor)
        return entry

    def list_trashed(self, *, org_id: Optional[str] = None) -> List[FileEntry]:
        trashed = [entry for entry in self._files.values() if entry.deleted_at]
        if org_id:
            trashed = [entry for entry in trashed if entry.org_id == org_id]
        floor = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(trashed, key=lambda e: e.deleted_at or floor, reverse=True)

    def restore_file(self, file_id: str, actor: str, *, target_parent: Optional[str] = None) -> FileEntry:
        entry = self._files[file_id]
        entry.deleted_at = None
        entry.deleted_by = None
        if target_parent is not None:
            entry.parent_id = target_parent
        entry.updated_at = datetime.now(timezone.utc)
        self.emit_event("file_restored", file_id=file_id, actor=actor)
        return entry

    def purge_expired_trash(self, *, retention_days: int) -> List[str]:
        if retention_days <= 0:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        removed: List[str] = []
        for file_id, entry in list(self._files.items()):
            if not entry.deleted_at or entry.deleted_at >= cutoff:
                continue
            removed.append(file_id)
            self._files.pop(file_id, None)
            self._current_manifests.pop(file_id, None)
            self._file_versions.pop(file_id, None)
            for manifest_id, manifest in list(self._manifests.items()):
                if manifest.file_id == file_id:
                    self._manifests.pop(manifest_id, None)
        if removed:
            self.emit_event("trash_purged", file_ids="|".join(removed))
        return removed
