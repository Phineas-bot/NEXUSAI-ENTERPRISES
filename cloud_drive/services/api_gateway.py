"""API gateway façade for clients."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterable, Optional, Tuple

from .metadata_service import MetadataService
from .upload_service import UploadOrchestrator
from .sharing_service import SharingService
from .search_service import SearchIndexService
from .activity_service import ActivityService
from .lifecycle_service import LifecycleManager
from .observability_service import ObservabilityManager, SLODefinition
from .backup_service import BackupManager, BackupSnapshot
from .capacity_planner import CapacityPlanner, CapacityRecommendation


@dataclass
class APIGateway:
    metadata_service: MetadataService
    upload_service: UploadOrchestrator
    sharing_service: SharingService
    search_service: SearchIndexService
    activity_service: ActivityService
    lifecycle_manager: LifecycleManager | None = None
    observability_manager: ObservabilityManager | None = None
    backup_manager: BackupManager | None = None
    capacity_planner: CapacityPlanner | None = None
    metrics_supplier: Callable[[], Dict[str, float]] | None = None

    # File/folder metadata -------------------------------------------------

    def create_folder(self, org_id: str, parent_id: Optional[str], name: str, created_by: str):
        entry = self.metadata_service.create_folder(org_id, parent_id, name, created_by)
        self._refresh_search_index(entry.id)
        return entry

    def get_file(self, file_id: str, *, include_deleted: bool = False):
        entry = self.metadata_service.get_file(file_id, include_deleted=include_deleted)
        if entry is None:
            raise KeyError(file_id)
        return entry

    def list_children(self, parent_id: Optional[str]):
        return self.metadata_service.list_children(parent_id)

    # Upload lifecycle -----------------------------------------------------

    def start_upload(
        self,
        org_id: str,
        parent_id: str,
        size_bytes: int,
        created_by: str,
        *,
        file_id: Optional[str] = None,
        chunk_size: Optional[int] = None,
        client_hints: Optional[dict[str, str]] = None,
        max_parallel_streams: Optional[int] = None,
    ):
        return self.upload_service.initiate_session(
            org_id,
            parent_id,
            size_bytes,
            created_by,
            file_id=file_id,
            chunk_size=chunk_size,
            client_hints=client_hints,
            max_parallel_streams=max_parallel_streams,
        )

    def append_chunk(
        self,
        session_id: str,
        source_node: str,
        file_name: str,
        chunk_bytes: int,
        *,
        chunk_id: Optional[int] = None,
        offset: Optional[int] = None,
        checksum: Optional[str] = None,
    ):
        self.upload_service.append_chunk(
            session_id,
            source_node,
            file_name,
            chunk_bytes,
            chunk_id=chunk_id,
            offset=offset,
            checksum=checksum,
        )

    def finalize_upload(self, session_id: str):
        manifest = self.upload_service.finalize(session_id)
        session = self.get_upload_session(session_id)
        self._refresh_search_index(session.file_id)
        return manifest

    def abort_upload(self, session_id: str) -> None:
        self.upload_service.abort(session_id)

    def get_upload_session(self, session_id: str):
        session = self.upload_service.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def describe_upload(self, session_id: str):
        if session_id not in self.upload_service.sessions:
            raise KeyError(session_id)
        return self.upload_service.describe_session(session_id)

    # Sharing ---------------------------------------------------------------

    def grant_share(
        self,
        file_id: str,
        *,
        principal_type: str = "user",
        principal_id: str,
        permission: str = "viewer",
        created_by: str,
        expires_at: Optional[datetime] = None,
        link_token: Optional[str] = None,
        password: Optional[str] = None,
    ):
        share = self.sharing_service.grant_access(
            file_id,
            principal_type=principal_type,
            principal_id=principal_id,
            permission=permission,
            created_by=created_by,
            expires_at=expires_at,
            link_token=link_token,
            password=password,
        )
        self._refresh_search_index(file_id)
        return share

    def list_shares(self, file_id: str):
        return self.sharing_service.list_grants(file_id)

    def revoke_share(self, file_id: str, share_id: str) -> None:
        self.sharing_service.revoke_access(file_id, share_id)
        self._refresh_search_index(file_id)

    # Trash lifecycle -------------------------------------------------------

    def trash_file(self, file_id: str, actor: str):
        entry = self.metadata_service.delete_file(file_id, actor)
        self.search_service.remove_file(file_id)
        return entry

    def list_trashed(self, *, org_id: Optional[str] = None):
        return self.metadata_service.list_trashed(org_id=org_id)

    def restore_file(self, file_id: str, actor: str, *, parent_id: Optional[str] = None):
        entry = self.metadata_service.restore_file(file_id, actor, target_parent=parent_id)
        self._refresh_search_index(file_id)
        return entry

    def purge_trash(self, retention_days: int) -> list[str]:
        removed = self.metadata_service.purge_expired_trash(retention_days=retention_days)
        for file_id in removed:
            self.search_service.remove_file(file_id)
            self.sharing_service.purge_file(file_id)
        return removed

    # Versions --------------------------------------------------------------

    def list_versions(self, file_id: str):
        return self.metadata_service.list_versions(file_id)

    def restore_version(self, file_id: str, version_id: str, actor: str):
        restored = self.metadata_service.restore_version(file_id, version_id, actor)
        if restored:
            self._refresh_search_index(file_id)
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
    ):
        return self.metadata_service.update_version_metadata(
            file_id,
            version_id,
            label=label,
            is_pinned=is_pinned,
            autosave=autosave,
            change_summary=change_summary,
        )

    # Activity & search -----------------------------------------------------

    def list_activity(self):
        return list(self.activity_service.events)

    def search(self, org_id: str, query: str):
        return self.search_service.search(org_id, query)

    # Operations & observability --------------------------------------------

    def get_observability_overview(self) -> dict:
        manager = self.observability_manager
        dashboards = dict(manager.dashboards) if manager else {}
        slos = [self._serialize_slo(slo) for slo in manager.slo_definitions] if manager else []
        alerts = list(manager.recent_alerts[-20:]) if manager else []
        metrics = self.metrics_supplier() if self.metrics_supplier else {}
        return {
            "dashboards": dashboards,
            "slos": slos,
            "alerts": alerts,
            "metrics": metrics,
        }

    def list_backups(self) -> dict:
        if not self.backup_manager:
            return {"snapshots": []}
        snapshots = [self._serialize_snapshot(snapshot) for snapshot in self.backup_manager.list_snapshots()]
        return {"snapshots": snapshots}

    def get_capacity_overview(self) -> dict:
        recommendations = []
        if self.capacity_planner:
            recommendations = [self._serialize_capacity(rec) for rec in self.capacity_planner.latest_recommendations]
        metrics = self.metrics_supplier() if self.metrics_supplier else {}
        return {"recommendations": recommendations, "metrics": metrics}

    def upsert_slo(self, *, name: str, metric: str, threshold: float, comparator: str, window_minutes: int) -> dict:
        if not self.observability_manager:
            raise RuntimeError("observability manager unavailable")
        slo = SLODefinition(
            name=name,
            metric=metric,
            threshold=threshold,
            comparator=comparator,
            window_minutes=window_minutes,
        )
        stored = self.observability_manager.upsert_slo(slo)
        return self._serialize_slo(stored)

    def delete_slo(self, name: str) -> dict:
        if not self.observability_manager:
            raise RuntimeError("observability manager unavailable")
        removed = self.observability_manager.remove_slo(name)
        return {"deleted": removed, "name": name}

    def upsert_dashboard(self, dashboard_id: str, definition: dict) -> dict:
        if not self.observability_manager:
            raise RuntimeError("observability manager unavailable")
        stored = self.observability_manager.register_dashboard(dashboard_id, definition)
        return {"dashboard_id": dashboard_id, "definition": stored}

    def delete_dashboard(self, dashboard_id: str) -> dict:
        if not self.observability_manager:
            raise RuntimeError("observability manager unavailable")
        removed = self.observability_manager.remove_dashboard(dashboard_id)
        return {"deleted": removed, "dashboard_id": dashboard_id}

    # Download streaming ----------------------------------------------------

    def stream_download(
        self,
        file_id: str,
        *,
        offset: int = 0,
        length: Optional[int] = None,
        chunk_size: Optional[int] = None,
    ) -> Iterable[Tuple[int, bytes, bool]]:
        entry = self.metadata_service.get_file(file_id)
        if entry is None or entry.deleted_at:
            raise KeyError(file_id)
        manifest = self.metadata_service.get_manifest(file_id)
        if manifest is None:
            raise KeyError(file_id)
        if offset < 0:
            raise ValueError("offset must be non-negative")
        total_size = manifest.total_size
        if offset > total_size:
            raise ValueError("offset beyond end of file")
        default_chunk = self.upload_service.config.storage.default_chunk_size
        target_chunk = chunk_size or default_chunk
        if target_chunk <= 0:
            target_chunk = default_chunk
        remaining = (length if length and length > 0 else total_size - offset)
        if remaining < 0:
            raise ValueError("length must be positive")
        if remaining == 0:
            yield offset, b"", True
            return

        if self.lifecycle_manager:
            self.lifecycle_manager.record_access(manifest.manifest_id)

        sorted_segments = sorted(manifest.segments, key=lambda seg: seg.offset)
        cursor = offset
        bytes_left = remaining

        for segment in sorted_segments:
            seg_start = segment.offset
            seg_end = seg_start + segment.length
            if seg_end <= cursor:
                continue
            if cursor < seg_start:
                cursor = seg_start

            while cursor < seg_end and bytes_left > 0:
                slice_len = min(target_chunk, seg_end - cursor, bytes_left)
                rel = cursor - seg_start
                chunk = self._build_chunk_payload(segment.node_id, segment.file_id, rel, slice_len)
                bytes_left -= slice_len
                is_last = bytes_left == 0
                yield cursor, chunk, is_last
                cursor += slice_len
            if bytes_left == 0:
                break

        if bytes_left > 0:
            raise RuntimeError("manifest missing requested range")

    @staticmethod
    def _build_chunk_payload(node_id: str, file_id: str, segment_offset: int, length: int) -> bytes:
        seed = f"{node_id}:{file_id}:{segment_offset}".encode()
        if not seed:
            seed = b"\x00"
        repeats = (length // len(seed)) + 1
        return (seed * repeats)[:length]

    def _refresh_search_index(self, file_id: Optional[str]) -> None:
        if not file_id:
            return
        entry = self.metadata_service.get_file(file_id, include_deleted=True)
        if entry is None or entry.deleted_at:
            self.search_service.remove_file(file_id)
            return
        versions = self.metadata_service.list_versions(file_id)
        shares = self.sharing_service.list_grants(file_id)
        self.search_service.index_file(entry, versions, shares)

    @staticmethod
    def _serialize_slo(slo: SLODefinition) -> dict:
        return {
            "name": slo.name,
            "metric": slo.metric,
            "threshold": slo.threshold,
            "comparator": slo.comparator,
            "window_minutes": slo.window_minutes,
        }

    @staticmethod
    def _serialize_snapshot(snapshot: BackupSnapshot) -> dict:
        return {
            "snapshot_id": snapshot.snapshot_id,
            "taken_at": snapshot.taken_at.isoformat(),
            "manifest_count": snapshot.manifest_count,
            "size_bytes": snapshot.size_bytes,
            "metadata_summary": snapshot.metadata_summary,
        }

    @staticmethod
    def _serialize_capacity(rec: CapacityRecommendation) -> dict:
        return {
            "resource": rec.resource,
            "action": rec.action,
            "reason": rec.reason,
        }
