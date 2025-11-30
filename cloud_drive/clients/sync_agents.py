"""Client-facing sync agents that wrap the uploader helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from .upload_clients import DesktopUploader, MobileUploader


@dataclass
class DesktopUploadTask:
    session_id: str
    file_path: Path
    source_node: str


@dataclass
class MobileUploadTask:
    session_id: str
    source_node: str
    chunk_provider: Callable[[int, int], bytes]


class DesktopSyncAgent:
    """Orchestrates resumable desktop uploads using gap maps."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: Optional[object] = None,
        uploader_cls: type[DesktopUploader] = DesktopUploader,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client
        self._uploader_cls = uploader_cls
        self._tasks: Dict[str, DesktopUploadTask] = {}

    def pending_sessions(self) -> Iterable[str]:
        return tuple(self._tasks.keys())

    def register_file(self, session_id: str, file_path: Path | str, source_node: str) -> None:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(path)
        self._tasks[session_id] = DesktopUploadTask(session_id=session_id, file_path=path, source_node=source_node)

    def resume_all(self) -> Dict[str, Dict[str, object]]:
        results: Dict[str, Dict[str, object]] = {}
        for session_id, task in list(self._tasks.items()):
            uploader = self._uploader_cls(
                base_url=self.base_url,
                session_id=session_id,
                source_node=task.source_node,
                file_path=task.file_path,
                http_client=self.http_client,
            )
            status = uploader.resume_missing_chunks()
            results[session_id] = status
            if not status.get("gap_map"):
                self._tasks.pop(session_id, None)
        return results


class MobileUploadAgent:
    """Coordinates mobile chunk providers with the gap-map status endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: Optional[object] = None,
        uploader_cls: type[MobileUploader] = MobileUploader,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client
        self._uploader_cls = uploader_cls
        self._tasks: Dict[str, MobileUploadTask] = {}

    def pending_sessions(self) -> Iterable[str]:
        return tuple(self._tasks.keys())

    def register_session(
        self,
        session_id: str,
        source_node: str,
        chunk_provider: Callable[[int, int], bytes],
    ) -> None:
        self._tasks[session_id] = MobileUploadTask(
            session_id=session_id,
            source_node=source_node,
            chunk_provider=chunk_provider,
        )

    def resume_all(self) -> Dict[str, Dict[str, object]]:
        results: Dict[str, Dict[str, object]] = {}
        for session_id, task in list(self._tasks.items()):
            uploader = self._uploader_cls(
                base_url=self.base_url,
                session_id=session_id,
                source_node=task.source_node,
                chunk_provider=task.chunk_provider,
                http_client=self.http_client,
            )
            status = uploader.resume_missing_chunks()
            results[session_id] = status
            if not status.get("gap_map"):
                self._tasks.pop(session_id, None)
        return results
