"""Backup and disaster recovery scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict

from .base import BaseService


@dataclass
class BackupSnapshot:
    snapshot_id: str
    taken_at: datetime
    manifest_count: int
    size_bytes: int
    metadata_summary: Dict[str, int]


@dataclass
class BackupManager(BaseService):
    snapshots: List[BackupSnapshot] = field(default_factory=list)
    retention_days: int = 30

    def run_backup(self, manifest_count: int, size_bytes: int, metadata_summary: Dict[str, int]) -> BackupSnapshot:
        snapshot = BackupSnapshot(
            snapshot_id=f"snap-{len(self.snapshots) + 1}",
            taken_at=datetime.now(timezone.utc),
            manifest_count=manifest_count,
            size_bytes=size_bytes,
            metadata_summary=metadata_summary,
        )
        self.snapshots.append(snapshot)
        self.telemetry.emit_event("backup_completed", {
            "snapshot_id": snapshot.snapshot_id,
            "manifests": str(manifest_count),
            "size_bytes": str(size_bytes),
        })
        self._purge_expired()
        return snapshot

    def latest(self) -> BackupSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def list_snapshots(self) -> List[BackupSnapshot]:
        return list(self.snapshots)

    def restore(self, snapshot_id: str) -> BackupSnapshot:
        for snapshot in self.snapshots:
            if snapshot.snapshot_id == snapshot_id:
                self.telemetry.emit_event("backup_restore_requested", {"snapshot_id": snapshot_id})
                return snapshot
        raise KeyError(snapshot_id)

    def _purge_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        self.snapshots = [snap for snap in self.snapshots if snap.taken_at >= cutoff]
