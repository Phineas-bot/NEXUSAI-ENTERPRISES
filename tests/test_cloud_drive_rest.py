"""FastAPI integration tests covering upload + download flow."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Ensure the embedded gRPC server binds to an ephemeral port during tests
os.environ.setdefault("CLOUD_DRIVE_GRPC_BIND", "127.0.0.1:0")

from cloud_drive.api.server import app, runtime  # noqa: E402  (env vars must be set first)


@pytest.fixture(scope="module")
def api_client():
    if "rest-node" not in runtime.controller.network.nodes:
        runtime.controller.add_node("rest-node")
    with TestClient(app) as client:
        yield client


def test_rest_download_flow(api_client: TestClient):
    folder_resp = api_client.post("/folders", json={"name": "rest-docs", "parent_id": None})
    folder_resp.raise_for_status()
    folder_id = folder_resp.json()["id"]

    session_resp = api_client.post(
        "/uploads:sessions",
        json={"parent_id": folder_id, "size_bytes": 1024, "chunk_size": 512},
    )
    session_resp.raise_for_status()
    session_payload = session_resp.json()
    session_id = session_payload["session_id"]
    assert session_payload["gap_map"], "Expected initial gap map entries"

    chunk_resp = api_client.post(
        "/uploads:chunk",
        json={
            "session_id": session_id,
            "source_node": "rest-node",
            "file_name": "rest.bin",
            "chunk_bytes": 512,
            "chunk_id": 0,
            "offset": 0,
        },
    )
    chunk_resp.raise_for_status()
    chunk_status = chunk_resp.json()
    assert chunk_status["received_bytes"] == 512
    assert len(chunk_status["gap_map"]) == 1

    status_resp = api_client.get(f"/uploads:sessions/{session_id}")
    status_resp.raise_for_status()
    assert status_resp.json()["received_bytes"] == 512

    chunk_resp = api_client.post(
        "/uploads:chunk",
        json={
            "session_id": session_id,
            "source_node": "rest-node",
            "file_name": "rest.bin",
            "chunk_bytes": 512,
            "chunk_id": 1,
            "offset": 512,
        },
    )
    chunk_resp.raise_for_status()
    assert chunk_resp.json()["gap_map"] == []

    finalize_resp = api_client.post(f"/uploads:finalize/{session_id}")
    finalize_resp.raise_for_status()
    operation = finalize_resp.json()["operation"]
    file_id = operation["metadata"]["resource_id"]

    download_resp = api_client.get(f"/files/{file_id}/download")
    assert download_resp.status_code == 200
    assert len(download_resp.content) == 1024

    partial_resp = api_client.get(f"/files/{file_id}/download", params={"offset": 256, "length": 128})
    assert partial_resp.status_code == 200
    assert len(partial_resp.content) == 128

    versions_resp = api_client.get(f"/files/{file_id}/versions")
    versions_resp.raise_for_status()
    versions = versions_resp.json()
    assert len(versions) == 1
    assert versions[0]["version_number"] == 1

    session_resp_v2 = api_client.post(
        "/uploads:sessions",
        json={"file_id": file_id, "parent_id": folder_id, "size_bytes": 1024, "chunk_size": 512},
    )
    session_resp_v2.raise_for_status()
    session_id_v2 = session_resp_v2.json()["session_id"]
    for chunk_id in (0, 1):
        api_client.post(
            "/uploads:chunk",
            json={
                "session_id": session_id_v2,
                "source_node": "rest-node",
                "file_name": f"rest-v2-{chunk_id}.bin",
                "chunk_bytes": 512,
                "chunk_id": chunk_id,
                "offset": chunk_id * 512,
            },
        ).raise_for_status()
    api_client.post(f"/uploads:finalize/{session_id_v2}").raise_for_status()

    versions_resp = api_client.get(f"/files/{file_id}/versions")
    versions_resp.raise_for_status()
    versions = versions_resp.json()
    assert len(versions) == 2
    assert versions[0]["version_number"] == 2

    base_version_id = next(v["version_id"] for v in versions if v["version_number"] == 1)
    restore_resp = api_client.post(f"/files/{file_id}/versions/{base_version_id}:restore")
    restore_resp.raise_for_status()
    restored_version = restore_resp.json()
    assert restored_version["version_number"] == 3
    versions_resp = api_client.get(f"/files/{file_id}/versions")
    versions_resp.raise_for_status()
    assert versions_resp.json()[0]["version_number"] == 3

    label_resp = api_client.post(
        f"/files/{file_id}/versions/{restored_version['version_id']}:label",
        json={"label": "baseline", "is_pinned": True},
    )
    label_resp.raise_for_status()
    assert label_resp.json()["label"] == "baseline"
    assert label_resp.json()["is_pinned"] is True

    search_resp = api_client.get("/search", params={"q": "rest"})
    search_resp.raise_for_status()
    assert any(doc["file_id"] == file_id for doc in search_resp.json())

    trash_resp = api_client.delete(f"/files/{file_id}")
    trash_resp.raise_for_status()
    assert trash_resp.json()["status"] == "trashed"

    trash_listing = api_client.get("/trash")
    trash_listing.raise_for_status()
    assert any(entry["id"] == file_id for entry in trash_listing.json())

    restore_resp = api_client.post(f"/trash/{file_id}:restore", json={"parent_id": folder_id})
    restore_resp.raise_for_status()
    assert restore_resp.json()["id"] == file_id

    post_restore_search = api_client.get("/search", params={"q": "rest"})
    post_restore_search.raise_for_status()
    assert any(doc["file_id"] == file_id for doc in post_restore_search.json())
