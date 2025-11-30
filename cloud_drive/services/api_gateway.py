"""API gateway façade for clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from .metadata_service import MetadataService
from .upload_service import UploadOrchestrator
from .sharing_service import SharingService
from .activity_service import ActivityService
from .lifecycle_service import LifecycleManager


@dataclass
class APIGateway:
    metadata_service: MetadataService
    upload_service: UploadOrchestrator
    sharing_service: SharingService
    activity_service: ActivityService
    lifecycle_manager: LifecycleManager | None = None

    def create_folder(self, org_id: str, parent_id: Optional[str], name: str, created_by: str):
        return self.metadata_service.create_folder(org_id, parent_id, name, created_by)

    def get_file(self, file_id: str):
        entry = self.metadata_service.get_file(file_id)
        if entry is None:
            raise KeyError(file_id)
        return entry

    def list_children(self, parent_id: Optional[str]):
        return self.metadata_service.list_children(parent_id)

    def start_upload(
        self,
        parent_id: str,
        size_bytes: int,
        created_by: str,
        *,
        chunk_size: Optional[int] = None,
        client_hints: Optional[dict[str, str]] = None,
        max_parallel_streams: Optional[int] = None,
    ):
        return self.upload_service.initiate_session(
            parent_id,
            size_bytes,
            created_by,
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
        return self.upload_service.finalize(session_id)

    def abort_upload(self, session_id: str):
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

    def grant_share(self, file_id: str, principal: str):
        self.sharing_service.grant_access(file_id, principal)

    def list_shares(self, file_id: str):
        return self.sharing_service.list_principals(file_id)

    def list_activity(self):
        return list(self.activity_service.events)

    def stream_download(
        self,
        file_id: str,
        *,
        offset: int = 0,
        length: Optional[int] = None,
        chunk_size: Optional[int] = None,
    ) -> Iterable[Tuple[int, bytes, bool]]:
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
