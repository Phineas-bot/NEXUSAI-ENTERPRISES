"""Client helpers for resumable uploads leveraging the status endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict

try:  # pragma: no cover - optional dependency for actual runtime use
    import requests
except ImportError:  # pragma: no cover
    requests = None


@dataclass
class UploadStatusClient:
    base_url: str
    session_id: str
    timeout: float = 5.0
    http_client: object = requests

    def _url(self, suffix: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}{suffix}"

    def fetch(self) -> Dict[str, object]:
        client = self._require_http()
        response = client.get(
            self._url(f"/uploads:sessions/{self.session_id}"),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def append_chunk(self, payload: Dict[str, object]) -> Dict[str, object]:
        client = self._require_http()
        response = client.post(
            self._url("/uploads:chunk"),
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _require_http(self):
        if self.http_client is None:
            raise RuntimeError("HTTP client unavailable; install requests or provide http_client")
        return self.http_client


@dataclass
class DesktopUploader:
    base_url: str
    session_id: str
    source_node: str
    file_path: Path
    http_client: object = requests

    def resume_missing_chunks(self) -> Dict[str, object]:
        status_client = UploadStatusClient(self.base_url, self.session_id, http_client=self.http_client)
        status = status_client.fetch()
        gap_map = status.get("gap_map", [])
        for gap in gap_map:
            chunk_id = gap["chunk_id"]
            offset = gap["offset"]
            length = gap["length"]
            if length <= 0:
                continue
            data = self._read_chunk(offset, length)
            payload = {
                "session_id": self.session_id,
                "source_node": self.source_node,
                "file_name": self.file_path.name,
                "chunk_bytes": len(data),
                "chunk_id": chunk_id,
                "offset": offset,
            }
            status_client.append_chunk(payload)
        return status_client.fetch()

    def _read_chunk(self, offset: int, length: int) -> bytes:
        with self.file_path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(length)
        if len(data) != length:
            raise RuntimeError("Local file missing bytes for resumable upload")
        return data


@dataclass
class MobileUploader:
    base_url: str
    session_id: str
    source_node: str
    chunk_provider: Callable[[int, int], bytes]
    http_client: object = requests

    def resume_missing_chunks(self) -> Dict[str, object]:
        status_client = UploadStatusClient(self.base_url, self.session_id, http_client=self.http_client)
        status = status_client.fetch()
        for gap in status.get("gap_map", []):
            data = self.chunk_provider(gap["offset"], gap["length"])
            payload = {
                "session_id": self.session_id,
                "source_node": self.source_node,
                "file_name": "mobile.bin",
                "chunk_bytes": len(data),
                "chunk_id": gap["chunk_id"],
                "offset": gap["offset"],
            }
            status_client.append_chunk(payload)
        return status_client.fetch()
