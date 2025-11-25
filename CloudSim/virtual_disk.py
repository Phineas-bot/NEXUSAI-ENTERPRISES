from __future__ import annotations

import hashlib
import heapq
import os
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from simulator import Simulator


class DiskCorruptionError(RuntimeError):
    """Raised when checksum verification fails or corruption is detected."""


@dataclass
class DiskIOProfile:
    throughput_bytes_per_sec: int = 200 * 1024 * 1024  # ~200MB/s default
    seek_time_ms: float = 2.5
    max_outstanding: int = 2


@dataclass
class DiskIOTicket:
    file_id: str
    chunk_id: int
    op_type: str
    completion_time: float
    size: int


def _default_checksum(payload: Optional[bytes], size: int) -> str:
    if payload is None:
        payload = bytes(size)
    return hashlib.sha256(payload).hexdigest()


@dataclass
class DiskChunk:
    size: int
    data: Optional[bytes] = None
    checksum: Optional[str] = None
    corrupted: bool = False


@dataclass
class DiskFile:
    file_id: str
    total_size: int
    committed_bytes: int = 0
    chunks: Dict[int, DiskChunk] = field(default_factory=dict)
    path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class VirtualDisk:
    def __init__(
        self,
        capacity_bytes: int,
        block_size: int = 4096,
        *,
        simulator: Optional["Simulator"] = None,
        io_profile: Optional[DiskIOProfile] = None,
        persist_root: Optional[str] = None,
        integrity_verification: bool = True,
    ):
        if capacity_bytes <= 0:
            raise ValueError("capacity_bytes must be positive")
        if block_size <= 0:
            raise ValueError("block_size must be positive")

        self.capacity_bytes = capacity_bytes
        self.block_size = block_size
        self.simulator = simulator
        self.io_profile = io_profile or DiskIOProfile()
        self.persist_root = persist_root
        self.integrity_verification = integrity_verification
        self._used_bytes = 0
        self._reserved_bytes = 0
        self._files: Dict[str, DiskFile] = {}
        self._directories: Dict[str, List[str]] = {"/": []}
        self._scheduled_ops: Dict[Tuple[str, int, str], DiskIOTicket] = {}
        channel_count = max(1, self.io_profile.max_outstanding)
        self._channel_available_times: List[float] = [0.0 for _ in range(channel_count)]
        heapq.heapify(self._channel_available_times)

    @property
    def used_bytes(self) -> int:
        return self._used_bytes

    @property
    def reserved_bytes(self) -> int:
        return self._reserved_bytes

    @property
    def free_bytes(self) -> int:
        return self.capacity_bytes - self._used_bytes - self._reserved_bytes

    def _normalize_path(self, path: str) -> str:
        pure = PurePosixPath("/" + path.lstrip("/"))
        return str(pure)

    def _ensure_directory(self, path: str) -> None:
        path = self._normalize_path(path)
        if path in self._directories:
            return
        parent = str(PurePosixPath(path).parent)
        if parent == path:
            parent = "/"
        if parent and parent not in self._directories:
            self._ensure_directory(parent)
        self._directories.setdefault(path, [])
        if parent in self._directories:
            name = PurePosixPath(path).name or "/"
            if name and name not in self._directories[parent]:
                self._directories[parent].append(name)

    def _track_path(self, file_path: str) -> None:
        directory = str(PurePosixPath(file_path).parent)
        name = PurePosixPath(file_path).name
        if not directory:
            directory = "/"
        self._ensure_directory(directory)
        children = self._directories.setdefault(directory, [])
        if name and name not in children:
            children.append(name)

    def has_capacity(self, size: int) -> bool:
        if size < 0:
            raise ValueError("size cannot be negative")
        return (self._used_bytes + self._reserved_bytes + size) <= self.capacity_bytes

    def _reserve_io_slot(self, size: int, current_time: float) -> float:
        size = max(1, size)
        available_at = heapq.heappop(self._channel_available_times)
        start_time = max(available_at, current_time)
        throughput = max(1, self.io_profile.throughput_bytes_per_sec)
        transfer_time = size / throughput
        seek_seconds = max(0.0, self.io_profile.seek_time_ms / 1000.0)
        completion_time = start_time + seek_seconds + transfer_time
        heapq.heappush(self._channel_available_times, completion_time)
        return completion_time

    def schedule_write(
        self,
        file_id: str,
        chunk_id: int,
        expected_size: int,
        *,
        current_time: float,
    ) -> DiskIOTicket:
        if file_id not in self._files:
            raise KeyError(f"file_id {file_id} is not reserved")
        if (file_id, chunk_id, "write") in self._scheduled_ops:
            raise ValueError(f"write already scheduled for {file_id}:{chunk_id}")
        completion_time = self._reserve_io_slot(expected_size, current_time)
        ticket = DiskIOTicket(
            file_id=file_id,
            chunk_id=chunk_id,
            op_type="write",
            completion_time=completion_time,
            size=expected_size,
        )
        self._scheduled_ops[(file_id, chunk_id, "write")] = ticket
        return ticket

    def schedule_read(
        self,
        file_id: str,
        chunk_id: int,
        *,
        current_time: float,
    ) -> DiskIOTicket:
        disk_file = self._files.get(file_id)
        if not disk_file or chunk_id not in disk_file.chunks:
            raise KeyError(f"chunk {chunk_id} not found for {file_id}")
        if (file_id, chunk_id, "read") in self._scheduled_ops:
            raise ValueError(f"read already scheduled for {file_id}:{chunk_id}")
        expected_size = disk_file.chunks[chunk_id].size
        completion_time = self._reserve_io_slot(expected_size, current_time)
        ticket = DiskIOTicket(
            file_id=file_id,
            chunk_id=chunk_id,
            op_type="read",
            completion_time=completion_time,
            size=expected_size,
        )
        self._scheduled_ops[(file_id, chunk_id, "read")] = ticket
        return ticket

    def complete_write(self, ticket: DiskIOTicket, data: Optional[bytes]) -> None:
        key = (ticket.file_id, ticket.chunk_id, "write")
        if key not in self._scheduled_ops:
            raise KeyError(f"No pending write for {ticket.file_id}:{ticket.chunk_id}")
        self._scheduled_ops.pop(key)
        self._commit_chunk(ticket.file_id, ticket.chunk_id, data, ticket.size)

    def complete_read(self, ticket: DiskIOTicket) -> bytes:
        key = (ticket.file_id, ticket.chunk_id, "read")
        if key not in self._scheduled_ops:
            raise KeyError(f"No pending read for {ticket.file_id}:{ticket.chunk_id}")
        self._scheduled_ops.pop(key)
        return self.read_chunk(ticket.file_id, ticket.chunk_id)

    def cancel_ticket(self, ticket: DiskIOTicket) -> None:
        key = (ticket.file_id, ticket.chunk_id, ticket.op_type)
        self._scheduled_ops.pop(key, None)

    def reserve_file(
        self,
        file_id: str,
        total_size: int,
        *,
        path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if total_size <= 0:
            raise ValueError("total_size must be positive")
        if file_id in self._files:
            raise ValueError(f"file_id {file_id} already reserved")
        if not self.has_capacity(total_size):
            return False

        normalized_path = self._normalize_path(path or file_id)
        self._track_path(normalized_path)
        self._files[file_id] = DiskFile(
            file_id=file_id,
            total_size=total_size,
            path=normalized_path,
            metadata=metadata or {},
        )
        self._reserved_bytes += total_size
        return True

    def write_chunk(
        self,
        file_id: str,
        chunk_id: int,
        data: Optional[bytes],
        expected_size: int,
    ) -> None:
        self._commit_chunk(file_id, chunk_id, data, expected_size)

    def _commit_chunk(
        self,
        file_id: str,
        chunk_id: int,
        data: Optional[bytes],
        expected_size: int,
    ) -> None:
        if expected_size <= 0:
            raise ValueError("expected_size must be positive")
        if file_id not in self._files:
            raise KeyError(f"file_id {file_id} is not reserved")

        disk_file = self._files[file_id]
        if chunk_id in disk_file.chunks:
            raise ValueError(f"chunk {chunk_id} already written for {file_id}")

        payload = data if data is not None else None
        if payload is not None and len(payload) != expected_size:
            raise ValueError("payload length mismatch")

        checksum = _default_checksum(payload, expected_size)
        disk_file.chunks[chunk_id] = DiskChunk(size=expected_size, data=payload, checksum=checksum)
        disk_file.committed_bytes += expected_size
        self._used_bytes += expected_size
        self._reserved_bytes -= expected_size
        if self._reserved_bytes < 0:
            self._reserved_bytes = 0
        if disk_file.committed_bytes > disk_file.total_size:
            raise ValueError("Committed more bytes than reserved for file")
        if self.persist_root and payload is not None:
            self._persist_chunk(disk_file, chunk_id, payload)

    def _persist_chunk(self, disk_file: DiskFile, chunk_id: int, payload: bytes) -> None:
        if not self.persist_root:
            return
        path = disk_file.path or disk_file.file_id
        relative = str(PurePosixPath(path).relative_to("/"))
        host_path = os.path.join(self.persist_root, relative)
        directory = os.path.dirname(host_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        mode = "r+b" if os.path.exists(host_path) else "wb"
        with open(host_path, mode) as handle:
            handle.seek(0, os.SEEK_END)
            handle.write(payload)

    def read_chunk(self, file_id: str, chunk_id: int) -> bytes:
        disk_file = self._files.get(file_id)
        if not disk_file or chunk_id not in disk_file.chunks:
            raise KeyError(f"chunk {chunk_id} not found for {file_id}")
        chunk = disk_file.chunks[chunk_id]
        if chunk.corrupted:
            raise DiskCorruptionError(f"chunk {chunk_id} corrupted for {file_id}")
        payload = chunk.data if chunk.data is not None else bytes(chunk.size)
        if self.integrity_verification and chunk.checksum:
            expected = _default_checksum(payload, chunk.size)
            if expected != chunk.checksum:
                chunk.corrupted = True
                raise DiskCorruptionError(f"Checksum mismatch for {file_id}:{chunk_id}")
        return payload

    def read_file(self, file_id: str) -> bytes:
        disk_file = self._files.get(file_id)
        if not disk_file:
            raise KeyError(f"file {file_id} not found")
        ordered_chunks = [self.read_chunk(file_id, chunk_id) for chunk_id in sorted(disk_file.chunks)]
        return b"".join(ordered_chunks)

    def chunk_checksum(self, file_id: str, chunk_id: int) -> Optional[str]:
        disk_file = self._files.get(file_id)
        if not disk_file:
            return None
        chunk = disk_file.chunks.get(chunk_id)
        return chunk.checksum if chunk else None

    def inject_corruption(self, file_id: str, chunk_id: int) -> None:
        disk_file = self._files.get(file_id)
        if not disk_file or chunk_id not in disk_file.chunks:
            raise KeyError(f"chunk {chunk_id} not found for {file_id}")
        disk_file.chunks[chunk_id].corrupted = True

    def recover_chunk(self, file_id: str, chunk_id: int, repaired_data: Optional[bytes] = None) -> None:
        disk_file = self._files.get(file_id)
        if not disk_file or chunk_id not in disk_file.chunks:
            raise KeyError(f"chunk {chunk_id} not found for {file_id}")
        chunk = disk_file.chunks[chunk_id]
        chunk.corrupted = False
        if repaired_data is not None:
            chunk.data = repaired_data
            chunk.checksum = _default_checksum(repaired_data, chunk.size)

    def release_file(self, file_id: str) -> None:
        disk_file = self._files.pop(file_id, None)
        if not disk_file:
            return
        remaining_reserved = max(0, disk_file.total_size - disk_file.committed_bytes)
        self._reserved_bytes -= remaining_reserved
        self._used_bytes -= disk_file.committed_bytes

    def delete_file(self, file_id: str) -> None:
        disk_file = self._files.get(file_id)
        if not disk_file:
            return
        self._used_bytes -= disk_file.committed_bytes
        self._files.pop(file_id, None)

    def flush(self) -> None:
        """Placeholder for flushing a persistent backend."""
        return

    def list_directory(self, path: str = "/") -> List[str]:
        normalized = self._normalize_path(path)
        return list(self._directories.get(normalized, []))

    def get_file_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        disk_file = self._files.get(file_id)
        if not disk_file:
            return None
        return {
            "path": disk_file.path,
            "total_size": disk_file.total_size,
            "committed_bytes": disk_file.committed_bytes,
            "metadata": dict(disk_file.metadata),
        }
