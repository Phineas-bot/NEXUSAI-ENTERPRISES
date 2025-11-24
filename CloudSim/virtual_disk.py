from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class DiskChunk:
    size: int
    data: Optional[bytes] = None


@dataclass
class DiskFile:
    file_id: str
    total_size: int
    committed_bytes: int = 0
    chunks: Dict[int, DiskChunk] = field(default_factory=dict)


class VirtualDisk:
    def __init__(self, capacity_bytes: int, block_size: int = 4096):
        if capacity_bytes <= 0:
            raise ValueError("capacity_bytes must be positive")
        if block_size <= 0:
            raise ValueError("block_size must be positive")

        self.capacity_bytes = capacity_bytes
        self.block_size = block_size
        self._used_bytes = 0
        self._reserved_bytes = 0
        self._files: Dict[str, DiskFile] = {}

    @property
    def used_bytes(self) -> int:
        return self._used_bytes

    @property
    def reserved_bytes(self) -> int:
        return self._reserved_bytes

    @property
    def free_bytes(self) -> int:
        return self.capacity_bytes - self._used_bytes - self._reserved_bytes

    def has_capacity(self, size: int) -> bool:
        if size < 0:
            raise ValueError("size cannot be negative")
        return (self._used_bytes + self._reserved_bytes + size) <= self.capacity_bytes

    def reserve_file(self, file_id: str, total_size: int) -> bool:
        if total_size <= 0:
            raise ValueError("total_size must be positive")
        if file_id in self._files:
            raise ValueError(f"file_id {file_id} already reserved")
        if not self.has_capacity(total_size):
            return False

        self._files[file_id] = DiskFile(file_id=file_id, total_size=total_size)
        self._reserved_bytes += total_size
        return True

    def write_chunk(
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

        disk_file.chunks[chunk_id] = DiskChunk(size=expected_size, data=payload)
        disk_file.committed_bytes += expected_size
        self._used_bytes += expected_size
        self._reserved_bytes -= expected_size

        if disk_file.committed_bytes > disk_file.total_size:
            raise ValueError("Committed more bytes than reserved for file")

    def read_chunk(self, file_id: str, chunk_id: int) -> bytes:
        disk_file = self._files.get(file_id)
        if not disk_file or chunk_id not in disk_file.chunks:
            raise KeyError(f"chunk {chunk_id} not found for {file_id}")
        chunk = disk_file.chunks[chunk_id]
        return chunk.data if chunk.data is not None else bytes(chunk.size)

    def read_file(self, file_id: str) -> bytes:
        disk_file = self._files.get(file_id)
        if not disk_file:
            raise KeyError(f"file {file_id} not found")
        ordered_chunks = [self.read_chunk(file_id, chunk_id) for chunk_id in sorted(disk_file.chunks)]
        return b"".join(ordered_chunks)

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
