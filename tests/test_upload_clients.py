from __future__ import annotations

from cloud_drive.clients.upload_clients import DesktopUploader, MobileUploader


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _StubHTTP:
    def __init__(self, get_responses):
        self.get_responses = list(get_responses)
        self.post_payloads = []

    def get(self, url, timeout):
        assert "uploads:sessions" in url
        payload = self.get_responses.pop(0)
        return _DummyResponse(payload)

    def post(self, url, json, timeout):
        assert url.endswith("/uploads:chunk")
        self.post_payloads.append(json)
        return _DummyResponse({"status": "queued", "gap_map": []})


def test_desktop_uploader_resumes_all_gaps(tmp_path):
    file_path = tmp_path / "resume.bin"
    file_path.write_bytes(b"abcd1234")
    http = _StubHTTP(
        [
            {
                "session_id": "sess-1",
                "gap_map": [
                    {"chunk_id": 0, "offset": 0, "length": 4},
                    {"chunk_id": 1, "offset": 4, "length": 4},
                ],
            },
            {"session_id": "sess-1", "gap_map": []},
        ]
    )
    uploader = DesktopUploader(
        base_url="http://localhost:8000",
        session_id="sess-1",
        source_node="node-a",
        file_path=file_path,
        http_client=http,
    )
    final_status = uploader.resume_missing_chunks()
    assert final_status["gap_map"] == []
    assert len(http.post_payloads) == 2
    assert http.post_payloads[0]["chunk_id"] == 0
    assert http.post_payloads[1]["offset"] == 4


def test_mobile_uploader_invokes_chunk_provider():
    provided_offsets = []

    def provider(offset, length):
        provided_offsets.append(offset)
        return b"x" * length

    http = _StubHTTP(
        [
            {
                "session_id": "sess-m",
                "gap_map": [
                    {"chunk_id": 0, "offset": 0, "length": 2},
                    {"chunk_id": 1, "offset": 2, "length": 2},
                ],
            },
            {"session_id": "sess-m", "gap_map": []},
        ]
    )
    uploader = MobileUploader(
        base_url="http://localhost:8000",
        session_id="sess-m",
        source_node="node-mobile",
        chunk_provider=provider,
        http_client=http,
    )
    final_status = uploader.resume_missing_chunks()
    assert final_status["gap_map"] == []
    assert provided_offsets == [0, 2]
    assert len(http.post_payloads) == 2