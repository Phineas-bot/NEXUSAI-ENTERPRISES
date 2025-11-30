"""Smoke tests for the Cloud Drive FastAPI gateway."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cloud_drive.runtime import CloudDriveRuntime
from cloud_drive.api import server as api_server


@pytest.fixture
def client() -> TestClient:
    # Re-bootstrap runtime each test for isolation.
    api_server.runtime = CloudDriveRuntime.bootstrap()
    return TestClient(api_server.app)


def test_folder_upload_and_activity_flow(client: TestClient) -> None:
    runtime = api_server.runtime
    runtime.controller.add_node("node1")

    folder_resp = client.post("/folders", json={"name": "docs", "parent_id": None})
    assert folder_resp.status_code == 200
    folder_id = folder_resp.json()["id"]

    session_resp = client.post(
        "/uploads:sessions",
        json={"parent_id": folder_id, "size_bytes": 1024, "chunk_size": 1024},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["session_id"]

    chunk_resp = client.post(
        "/uploads:chunk",
        json={
            "session_id": session_id,
            "source_node": "node1",
            "file_name": "example.bin",
            "chunk_bytes": 1024,
        },
    )
    assert chunk_resp.status_code == 200

    finalize_resp = client.post(f"/uploads:finalize/{session_id}")
    assert finalize_resp.status_code == 200

    share_resp = client.post(f"/files/{folder_id}:share", json={"principal": "user@example.com"})
    assert share_resp.status_code == 200

    activity_resp = client.get("/activity")
    assert activity_resp.status_code == 200
    events = activity_resp.json()
    assert events, "Expected activity events to be recorded"
