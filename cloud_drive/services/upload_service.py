"""Upload orchestrator scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import uuid
from typing import Dict, List, Optional

from ..config import CloudDriveConfig
from ..messaging import InMemoryBus, MessageEnvelope
from ..models import ChunkStatus, FileManifest, ManifestSegment, UploadSession
from ..telemetry import TelemetryCollector
from .base import BaseService
from ..cloudsim import CloudSimController
from .metadata_service import MetadataService
from .replica_service import ReplicaManager
from .lifecycle_service import LifecycleManager
from .durability_service import DurabilityManager

SESSION_TTL = timedelta(hours=4)


@dataclass
class UploadOrchestrator(BaseService):
    controller: CloudSimController
    bus: InMemoryBus
    metadata_service: MetadataService
    replica_manager: ReplicaManager | None = None
    lifecycle_manager: LifecycleManager | None = None
    durability_manager: DurabilityManager | None = None
    sessions: Dict[str, UploadSession] = None

    def __post_init__(self) -> None:
        if self.sessions is None:
            self.sessions = {}

    def initiate_session(
        self,
        org_id: str,
        parent_id: str,
        size_bytes: int,
        created_by: str,
        *,
        file_id: Optional[str] = None,
        chunk_size: int | None = None,
        client_hints: Optional[Dict[str, str]] = None,
        max_parallel_streams: Optional[int] = None,
    ) -> UploadSession:
        session_id = str(uuid.uuid4())
        negotiated_chunk = self._negotiate_chunk_size(size_bytes, chunk_size, client_hints)
        streams = max_parallel_streams or self._suggest_parallel_streams(size_bytes, client_hints)
        now = self._now()
        session = UploadSession(
            session_id=session_id,
            file_id=file_id,
            org_id=org_id,
            parent_id=parent_id,
            expected_size=size_bytes,
            chunk_size=negotiated_chunk,
            created_by=created_by,
            expires_at=now + SESSION_TTL,
            created_at=now,
            max_parallel_streams=streams,
            client_hints=client_hints or {},
            last_activity_at=now,
        )
        self.sessions[session_id] = session
        self.emit_event("upload_session_initiated", session_id=session_id)
        return session

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
    ) -> None:
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")
        session = self.sessions[session_id]
        self._ensure_active(session)
        resolved_chunk_id = self._derive_chunk_id(session, chunk_id, offset)
        resolved_offset = offset if offset is not None else resolved_chunk_id * session.chunk_size
        chunk_entry = session.chunks.get(resolved_chunk_id)
        if chunk_entry and chunk_entry.status == "committed":
            # Idempotent retry; nothing to do besides refreshing activity timestamps.
            session.last_activity_at = self._now()
            return
        if chunk_entry and (chunk_entry.offset != resolved_offset or chunk_entry.length != chunk_bytes):
            raise RuntimeError("Chunk metadata mismatch for session %s" % session_id)
        if chunk_entry is None:
            chunk_entry = ChunkStatus(
                chunk_id=resolved_chunk_id,
                offset=resolved_offset,
                length=chunk_bytes,
                checksum=checksum,
            )
            session.chunks[resolved_chunk_id] = chunk_entry

        prev_status = chunk_entry.status
        chunk_entry.status = "committed"
        chunk_entry.last_updated_at = self._now()
        if prev_status != "committed":
            session.received_bytes += chunk_entry.length

        session.source_node = session.source_node or source_node
        session.file_name = session.file_name or file_name
        session.last_activity_at = self._now()
        if session.received_bytes > session.expected_size:
            raise RuntimeError("Chunk exceeds negotiated upload size")

        if not self._gap_map(session) and session.received_bytes >= session.expected_size:
            session.status = "ready"

        self.bus.publish(
            MessageEnvelope(
                topic="ingest.requests",
                payload={
                    "session_id": session_id,
                    "chunk_id": resolved_chunk_id,
                    "offset": resolved_offset,
                    "length": chunk_bytes,
                },
            )
        )

    def finalize(self, session_id: str) -> FileManifest:
        session = self.sessions[session_id]
        if session.status != "ready":
            raise RuntimeError("Upload incomplete")
        manifest = self._materialize_manifest(session)
        if session.file_id:
            manifest.file_id = session.file_id
        else:
            session.file_id = manifest.file_id
        self.metadata_service.register_manifest(manifest)
        session.manifest_id = manifest.manifest_id
        mime_type = self._infer_mime_type(session.file_name)
        self.metadata_service.ensure_file_entry(
            file_id=session.file_id,
            org_id=session.org_id,
            parent_id=session.parent_id,
            name=session.file_name or f"object-{session.session_id}",
            mime_type=mime_type,
            size_bytes=session.expected_size,
            created_by=session.created_by,
        )
        if self.replica_manager:
            manifest = self.replica_manager.enforce_policy(manifest)
        if self.lifecycle_manager:
            manifest = self.lifecycle_manager.apply_post_upload(
                manifest,
                accessed_by=session.created_by,
            )
        if self.durability_manager:
            manifest = self.durability_manager.apply(manifest, actor=session.created_by)
        self.metadata_service.upsert_manifest(manifest)
        self.metadata_service.record_version(
            file_id=session.file_id,
            manifest_id=manifest.manifest_id,
            size_bytes=session.expected_size,
            actor=session.created_by,
            change_summary="upload",
        )
        session.status = "finalized"
        self.bus.publish(MessageEnvelope(topic="replication.requests", payload={"session_id": session_id}))
        self.emit_metric("upload_finalize", 1, session_id=session_id)
        duration = self._now() - session.created_at
        latency_ms = max(0.0, duration.total_seconds() * 1000)
        self.emit_metric("ingest.latency_ms", latency_ms, org_id=session.org_id)
        return manifest

    def abort(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            session.status = "aborted"
            self.bus.publish(MessageEnvelope(topic="uploads.expired", payload={"session_id": session_id}))

    def describe_session(self, session_id: str) -> Dict[str, object]:
        session = self.sessions[session_id]
        gaps = self._gap_map(session)
        total_chunks = self._expected_chunk_count(session)
        committed = sum(1 for chunk in session.chunks.values() if chunk.status == "committed")
        return {
            "session_id": session.session_id,
            "parent_id": session.parent_id,
            "expected_size": session.expected_size,
            "chunk_size": session.chunk_size,
            "max_parallel_streams": session.max_parallel_streams,
            "received_bytes": session.received_bytes,
            "status": session.status,
            "expires_at": session.expires_at.isoformat(),
            "last_activity_at": session.last_activity_at.isoformat(),
            "total_chunks": total_chunks,
            "committed_chunks": committed,
            "gap_map": gaps,
            "client_hints": session.client_hints,
        }

    def _materialize_manifest(self, session: UploadSession) -> FileManifest:
        file_name = session.file_name or f"object-{session.session_id}"
        source_node = session.source_node or self._default_source_node()
        result = self.controller.network.ingest_file(
            source_node,
            file_name,
            session.expected_size,
            prefer_local=True,
        )
        if not result:
            raise RuntimeError("Unable to persist file into storage fabric")
        _, transfer = result
        manifest_id = transfer.backing_file_id or transfer.file_id
        manifest = self.controller.network.file_manifests_by_id.get(manifest_id)
        if manifest is None:
            raise RuntimeError("Storage fabric did not register manifest")
        lifecycle_policy = self.config.storage.lifecycle_policy
        hot_tier = lifecycle_policy.hot_storage_tier if lifecycle_policy else "hot"
        segments = [
            ManifestSegment(
                node_id=segment.node_id,
                file_id=segment.file_id,
                offset=segment.offset,
                length=segment.size,
                storage_tier=hot_tier,
                zone=getattr(self.controller.network.nodes.get(segment.node_id), "zone", None),
            )
            for segment in sorted(manifest.segments, key=lambda s: s.offset)
        ]
        return FileManifest(
            manifest_id=manifest.master_id,
            file_id=manifest.master_id,
            total_size=manifest.total_size,
            segments=segments,
        )

    @staticmethod
    def _infer_mime_type(file_name: Optional[str]) -> str:
        if not file_name:
            return "application/octet-stream"
        lowered = file_name.lower()
        if lowered.endswith(".txt"):
            return "text/plain"
        if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
            return "image/jpeg"
        if lowered.endswith(".png"):
            return "image/png"
        if lowered.endswith(".pdf"):
            return "application/pdf"
        return "application/octet-stream"

    def _default_source_node(self) -> str:
        nodes = list(self.controller.network.nodes.keys())
        if not nodes:
            raise RuntimeError("No storage nodes available for persistence")
        return nodes[0]

    def _negotiate_chunk_size(
        self,
        size_bytes: int,
        requested_chunk_size: Optional[int],
        client_hints: Optional[Dict[str, str]],
    ) -> int:
        if requested_chunk_size and requested_chunk_size > 0:
            return min(requested_chunk_size, self.config.storage.max_chunk_size)
        base = self.config.storage.default_chunk_size
        if not client_hints:
            return min(base, size_bytes)
        network_type = client_hints.get("network_type")
        device_class = client_hints.get("device_class")
        if network_type == "mobile":
            return min(2 * 1024 * 1024, size_bytes)
        if device_class == "workstation" and size_bytes >= 64 * 1024 * 1024:
            return min(32 * 1024 * 1024, size_bytes)
        return min(base, size_bytes)

    def _suggest_parallel_streams(self, size_bytes: int, client_hints: Optional[Dict[str, str]]) -> int:
        if client_hints and client_hints.get("network_type") == "mobile":
            return 2
        if size_bytes >= 512 * 1024 * 1024:
            return 8
        if size_bytes >= 64 * 1024 * 1024:
            return 4
        return 2

    def _derive_chunk_id(self, session: UploadSession, chunk_id: Optional[int], offset: Optional[int]) -> int:
        if chunk_id is not None and chunk_id >= 0:
            return chunk_id
        if offset is not None and session.chunk_size:
            return offset // session.chunk_size
        return len(session.chunks)

    def _gap_map(self, session: UploadSession) -> List[Dict[str, int]]:
        chunk_size = session.chunk_size or self.config.storage.default_chunk_size
        total_chunks = self._expected_chunk_count(session)
        gaps: List[Dict[str, int]] = []
        for cid in range(total_chunks):
            chunk = session.chunks.get(cid)
            if chunk and chunk.status == "committed":
                continue
            offset = cid * chunk_size
            remaining = max(session.expected_size - offset, 0)
            length = min(chunk_size, remaining) if remaining else 0
            gaps.append({
                "chunk_id": cid,
                "offset": offset,
                "length": length,
            })
        return gaps

    def _expected_chunk_count(self, session: UploadSession) -> int:
        chunk_size = session.chunk_size or self.config.storage.default_chunk_size
        if chunk_size <= 0:
            return 1
        return max(1, math.ceil(session.expected_size / chunk_size))

    def _ensure_active(self, session: UploadSession) -> None:
        now = self._now()
        if session.expires_at < now:
            raise RuntimeError("Upload session expired")
        session.expires_at = max(session.expires_at, now + timedelta(minutes=30))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
