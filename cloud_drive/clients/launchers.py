"""CLI launchers that connect stalled-upload detectors to sync agents.

The desktop and mobile background services watch a directory of JSON
"envelopes" produced by the native OS file watchers. Each envelope should
contain at minimum a ``session_id`` and ``source_node`` field; desktop agents
also require ``file_path`` pointing at the local staged file, while mobile
agents expect ``chunk_path`` representing the staging buffer to read from.

Example envelope stored under ``queue_dir/sess-123.json``::

    {
        "session_id": "sess-123",
        "source_node": "node-a",
        "file_path": "C:/Users/Alice/Uploads/video.mov"
    }

As the service successfully closes the gap map for a session it deletes the
matching JSON file, letting the OS watcher know the resume is complete.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from .sync_agents import DesktopSyncAgent, MobileUploadAgent
from ..config import ObservabilityConfig
from ..telemetry import TelemetryCollector

try:  # pragma: no cover - requests is optional for tests
    import requests
except ImportError:  # pragma: no cover
    requests = None

_LOGGER = logging.getLogger("cloud_drive.clients.launchers")


@dataclass
class QueueItem:
    session_id: str
    source_node: str
    file_path: Optional[Path] = None
    chunk_path: Optional[Path] = None
    path: Optional[Path] = None


class SessionQueue:
    """Simple directory-based queue for stalled upload envelopes."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def scan(self) -> Dict[str, QueueItem]:
        entries: Dict[str, QueueItem] = {}
        for json_file in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                _LOGGER.warning("Failed to read envelope %s: %s", json_file, exc)
                continue
            session_id = payload.get("session_id") or json_file.stem
            source_node = payload.get("source_node")
            if not session_id or not source_node:
                _LOGGER.warning("Ignoring envelope %s missing required fields", json_file)
                continue
            file_path = payload.get("file_path")
            chunk_path = payload.get("chunk_path")
            entries[session_id] = QueueItem(
                session_id=session_id,
                source_node=source_node,
                file_path=Path(file_path) if file_path else None,
                chunk_path=Path(chunk_path) if chunk_path else None,
                path=json_file,
            )
        return entries

    def remove(self, item: QueueItem) -> None:
        if item.path and item.path.exists():
            try:
                item.path.unlink()
            except OSError as exc:  # pragma: no cover - best effort cleanup
                _LOGGER.warning("Failed to remove envelope %s: %s", item.path, exc)


class AgentService:
    """Base service containing shared plumbing for telemetry + queue polling."""

    def __init__(
        self,
        queue: SessionQueue,
        telemetry: Optional[TelemetryCollector] = None,
    ) -> None:
        self.queue = queue
        self.telemetry = telemetry or TelemetryCollector(ObservabilityConfig())

    def run(self, poll_interval: float = 5.0) -> None:
        _LOGGER.info("Starting %s with poll interval %.1fs", self.__class__.__name__, poll_interval)
        try:
            while True:
                self.tick()
                time.sleep(poll_interval)
        except KeyboardInterrupt:  # pragma: no cover - manual shutdown
            _LOGGER.info("%s interrupted", self.__class__.__name__)

    def tick(self) -> None:
        raise NotImplementedError

    def _record_tick(self, span_seconds: float, summary: Dict[str, int], agent: str) -> None:
        labels = {"agent": agent}
        self.telemetry.emit_metric("client_agent.tick_seconds", span_seconds, labels)
        for key, value in summary.items():
            metric_name = f"client_agent.{key}"
            self.telemetry.emit_metric(metric_name, value, labels)


class DesktopAgentService(AgentService):
    """Wraps :class:`DesktopSyncAgent` with queue + telemetry plumbing."""

    def __init__(
        self,
        base_url: str,
        queue: SessionQueue,
        http_client: Optional[object] = None,
        telemetry: Optional[TelemetryCollector] = None,
        agent: Optional[DesktopSyncAgent] = None,
    ) -> None:
        super().__init__(queue, telemetry)
        self.agent = agent or DesktopSyncAgent(base_url, http_client=http_client or _build_http_client())

    def tick(self) -> None:
        envelopes = self.queue.scan()
        for item in envelopes.values():
            if not item.file_path or not item.file_path.exists():
                _LOGGER.debug("Skipping session %s because file %s is missing", item.session_id, item.file_path)
                continue
            self.agent.register_file(item.session_id, item.file_path, item.source_node)

        started = time.perf_counter()
        summary: Dict[str, int] = {"sessions_attempted": 0, "sessions_completed": 0}
        try:
            states = self.agent.resume_all()
        except Exception as exc:  # pragma: no cover - network issues
            _LOGGER.exception("Desktop agent tick failed: %s", exc)
            self.telemetry.emit_event("desktop_agent.error", {"message": str(exc)})
            return
        finally:
            elapsed = time.perf_counter() - started

        summary["sessions_attempted"] = len(states)
        for session_id, status in states.items():
            if not status.get("gap_map"):
                summary["sessions_completed"] += 1
                item = envelopes.get(session_id)
                if item:
                    self.queue.remove(item)

        self._record_tick(elapsed, summary, agent="desktop")


class MobileAgentService(AgentService):
    """Runs :class:`MobileUploadAgent` based on JSON envelopes."""

    def __init__(
        self,
        base_url: str,
        queue: SessionQueue,
        http_client: Optional[object] = None,
        telemetry: Optional[TelemetryCollector] = None,
        agent: Optional[MobileUploadAgent] = None,
    ) -> None:
        super().__init__(queue, telemetry)
        uploader = agent or MobileUploadAgent(base_url, http_client=http_client or _build_http_client())
        self.agent = uploader

    def tick(self) -> None:
        envelopes = self.queue.scan()
        for item in envelopes.values():
            if not item.chunk_path or not item.chunk_path.exists():
                _LOGGER.debug("Skipping session %s because chunk buffer %s is missing", item.session_id, item.chunk_path)
                continue
            provider = _file_chunk_provider(item.chunk_path)
            self.agent.register_session(item.session_id, item.source_node, provider)

        started = time.perf_counter()
        summary: Dict[str, int] = {"sessions_attempted": 0, "sessions_completed": 0}
        try:
            states = self.agent.resume_all()
        except Exception as exc:  # pragma: no cover - network/runtime issues
            _LOGGER.exception("Mobile agent tick failed: %s", exc)
            self.telemetry.emit_event("mobile_agent.error", {"message": str(exc)})
            return
        finally:
            elapsed = time.perf_counter() - started

        summary["sessions_attempted"] = len(states)
        for session_id, status in states.items():
            if not status.get("gap_map"):
                summary["sessions_completed"] += 1
                item = envelopes.get(session_id)
                if item:
                    self.queue.remove(item)

        self._record_tick(elapsed, summary, agent="mobile")


def _file_chunk_provider(path: Path):
    def _provider(offset: int, length: int) -> bytes:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(length)
        if len(data) != length:
            raise RuntimeError(f"Chunk provider short read for {path}")
        return data

    return _provider


def _build_http_client():
    if requests is None:
        return None
    return requests.Session()


def run_cli(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Cloud Drive client agents")
    sub = parser.add_subparsers(dest="command", required=True)

    def _shared(subparser):
        subparser.add_argument("--base-url", required=True, help="Cloud Drive REST endpoint")
        subparser.add_argument("--queue-dir", required=True, help="Directory containing stalled session envelopes")
        subparser.add_argument("--poll-interval", type=float, default=5.0)

    desktop = sub.add_parser("desktop", help="Run the desktop sync agent service")
    _shared(desktop)
    desktop.set_defaults(handler=_run_desktop)

    mobile = sub.add_parser("mobile", help="Run the mobile upload agent service")
    _shared(mobile)
    mobile.set_defaults(handler=_run_mobile)

    args = parser.parse_args(list(argv) if argv is not None else None)
    args.handler(args)


def _run_desktop(args: argparse.Namespace) -> None:
    queue = SessionQueue(Path(args.queue_dir))
    service = DesktopAgentService(args.base_url, queue)
    service.run(poll_interval=args.poll_interval)


def _run_mobile(args: argparse.Namespace) -> None:
    queue = SessionQueue(Path(args.queue_dir))
    service = MobileAgentService(args.base_url, queue)
    service.run(poll_interval=args.poll_interval)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    run_cli()
