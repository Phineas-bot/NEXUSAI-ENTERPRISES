from __future__ import annotations

import json
from pathlib import Path

from cloud_drive.clients.launchers import DesktopAgentService, MobileAgentService, SessionQueue
from cloud_drive.clients.sync_agents import DesktopSyncAgent, MobileUploadAgent
from cloud_drive.config import ObservabilityConfig
from cloud_drive.telemetry import TelemetryCollector


class _DesktopStub:
    def __init__(self, base_url, session_id, source_node, file_path, http_client):  # noqa: D401
        self.base_url = base_url
        self.session_id = session_id
        self.source_node = source_node
        self.file_path = Path(file_path)
        self.http_client = http_client
        _DesktopStub.calls.append(self)

    def resume_missing_chunks(self):
        payload = _DesktopStub.responses.pop(0)
        return payload


class _MobileStub:
    def __init__(self, base_url, session_id, source_node, chunk_provider, http_client):
        self.base_url = base_url
        self.session_id = session_id
        self.source_node = source_node
        self.chunk_provider = chunk_provider
        self.http_client = http_client
        _MobileStub.calls.append(self)

    def resume_missing_chunks(self):
        return _MobileStub.responses.pop(0)


class _CallTracker:
    calls: list
    responses: list


def setup_function(_):
    _DesktopStub.calls = []
    _DesktopStub.responses = []
    _MobileStub.calls = []
    _MobileStub.responses = []


def test_desktop_agent_registers_and_completes(tmp_path):
    file_path = tmp_path / "doc.bin"
    file_path.write_bytes(b"abcd" * 4)
    _DesktopStub.responses = [{"gap_map": []}]
    agent = DesktopSyncAgent(
        "http://api.local",
        http_client=object(),
        uploader_cls=_DesktopStub,
    )
    agent.register_file("sess-1", file_path, "node-a")
    results = agent.resume_all()
    assert "sess-1" not in agent.pending_sessions()
    assert results["sess-1"] == {"gap_map": []}
    assert len(_DesktopStub.calls) == 1
    call = _DesktopStub.calls[0]
    assert call.session_id == "sess-1"
    assert call.file_path == file_path


def test_desktop_agent_keeps_session_when_gaps_remain(tmp_path):
    file_path = tmp_path / "pending.bin"
    file_path.write_bytes(b"data")
    _DesktopStub.responses = [{"gap_map": [{"chunk_id": 1}]}]
    agent = DesktopSyncAgent(
        "http://api.local",
        uploader_cls=_DesktopStub,
    )
    agent.register_file("sess-hold", file_path, "node-h")
    agent.resume_all()
    assert "sess-hold" in agent.pending_sessions()


def test_mobile_agent_invokes_chunk_provider():
    provided = []

    def chunk_provider(offset, length):
        provided.append((offset, length))
        return b"x" * length

    _MobileStub.responses = [{"gap_map": []}]
    agent = MobileUploadAgent(
        "http://api.local",
        uploader_cls=_MobileStub,
    )
    agent.register_session("sess-m", "node-mobile", chunk_provider)
    agent.resume_all()
    assert len(_MobileStub.calls) == 1
    assert provided == []  # provider invoked lazily inside uploader stub


def test_mobile_agent_retains_session_with_pending_gaps():
    def chunk_provider(offset, length):
        return b"x" * length

    _MobileStub.responses = [{"gap_map": [{"chunk_id": 0}]}]
    agent = MobileUploadAgent("http://api.local", uploader_cls=_MobileStub)
    agent.register_session("sess-late", "node-m", chunk_provider)
    agent.resume_all()
    assert "sess-late" in agent.pending_sessions()


class _StubDesktopAgent:
    def __init__(self, responses):
        self.responses = responses
        self.registered = set()

    def register_file(self, session_id, file_path, source_node):  # noqa: D401
        self.registered.add(session_id)

    def resume_all(self):
        return {sid: self.responses.get(sid, {"gap_map": [{"chunk_id": 0}]}) for sid in self.registered}


class _StubMobileAgent:
    def __init__(self, responses):
        self.responses = responses
        self.registered = set()

    def register_session(self, session_id, source_node, chunk_provider):  # noqa: D401
        self.registered.add(session_id)

    def resume_all(self):
        return {sid: self.responses.get(sid, {"gap_map": [{"chunk_id": 1}]}) for sid in self.registered}


def test_session_queue_filters_invalid_files(tmp_path):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "bad.json").write_text("{", encoding="utf-8")
    (queue_dir / "missing.json").write_text(json.dumps({"session_id": "abc"}), encoding="utf-8")
    valid_file = queue_dir / "sess-ok.json"
    valid_file.write_text(
        json.dumps({"session_id": "sess-ok", "source_node": "node-a", "file_path": str(tmp_path / "a.bin")}),
        encoding="utf-8",
    )
    queue = SessionQueue(queue_dir)
    entries = queue.scan()
    assert set(entries) == {"sess-ok"}


def test_desktop_service_consumes_completed_sessions(tmp_path):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    local_file = tmp_path / "doc.bin"
    local_file.write_bytes(b"abc")
    envelope = queue_dir / "sess-done.json"
    envelope.write_text(
        json.dumps({"session_id": "sess-done", "source_node": "node-a", "file_path": str(local_file)}),
        encoding="utf-8",
    )
    telemetry = TelemetryCollector(ObservabilityConfig())
    responses = {"sess-done": {"gap_map": []}}
    service = DesktopAgentService(
        base_url="http://api.local",
        queue=SessionQueue(queue_dir),
        telemetry=telemetry,
        agent=_StubDesktopAgent(responses),
    )
    service.tick()
    assert not envelope.exists()
    assert any(metric["name"].startswith("client_agent") for metric in telemetry.metrics)


def test_mobile_service_keeps_pending_sessions(tmp_path):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    chunk_file = tmp_path / "mobile.bin"
    chunk_file.write_bytes(b"abcd")
    envelope = queue_dir / "sess-late.json"
    envelope.write_text(
        json.dumps({"session_id": "sess-late", "source_node": "node-m", "chunk_path": str(chunk_file)}),
        encoding="utf-8",
    )
    telemetry = TelemetryCollector(ObservabilityConfig())
    responses = {"sess-late": {"gap_map": [{"chunk_id": 1}]}}
    service = MobileAgentService(
        base_url="http://api.local",
        queue=SessionQueue(queue_dir),
        telemetry=telemetry,
        agent=_StubMobileAgent(responses),
    )
    service.tick()
    assert envelope.exists()
