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

    share_resp = client.post(
        f"/files/{folder_id}:share",
        json={"principal": "user@example.com", "permission": "editor"},
    )
    assert share_resp.status_code == 200
    share_id = share_resp.json()["share_id"]

    list_resp = client.get(f"/files/{folder_id}/shares")
    assert list_resp.status_code == 200
    assert any(grant["share_id"] == share_id for grant in list_resp.json())

    revoke_resp = client.delete(f"/files/{folder_id}/shares/{share_id}")
    assert revoke_resp.status_code == 200

    activity_resp = client.get("/activity")
    assert activity_resp.status_code == 200
    events = activity_resp.json()
    assert events, "Expected activity events to be recorded"


def test_ops_endpoints_expose_insights(client: TestClient) -> None:
    runtime = api_server.runtime
    runtime.controller.add_node("node1")
    admin_headers = {"x-user-roles": "ops.admin"}

    folder_resp = client.post("/folders", json={"name": "ops", "parent_id": None})
    assert folder_resp.status_code == 200
    folder_id = folder_resp.json()["id"]

    session_resp = client.post(
        "/uploads:sessions",
        json={"parent_id": folder_id, "size_bytes": 2048, "chunk_size": 1024},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["session_id"]

    chunk_a = client.post(
        "/uploads:chunk",
        json={
            "session_id": session_id,
            "source_node": "node1",
            "file_name": "ops.bin",
            "chunk_bytes": 1024,
        },
    )
    assert chunk_a.status_code == 200
    chunk_b = client.post(
        "/uploads:chunk",
        json={
            "session_id": session_id,
            "source_node": "node1",
            "file_name": "ops.bin",
            "chunk_bytes": 1024,
        },
    )
    assert chunk_b.status_code == 200

    finalize_resp = client.post(f"/uploads:finalize/{session_id}")
    assert finalize_resp.status_code == 200

    runtime.run_background_jobs()

    obs_resp = client.get("/ops/observability")
    assert obs_resp.status_code == 200
    obs_payload = obs_resp.json()
    assert "metrics" in obs_payload
    assert "ingest.p95_ms" in obs_payload["metrics"]
    assert obs_payload.get("dashboards"), "Expected default dashboards to be present"
    slo_names = {slo["name"] for slo in obs_payload.get("slos", [])}
    assert "upload_latency" in slo_names, "Default upload latency SLO missing"

    backup_resp = client.get("/ops/backups")
    assert backup_resp.status_code == 200
    snapshots = backup_resp.json().get("snapshots", [])
    assert snapshots, "Expected at least one backup snapshot"

    capacity_resp = client.get("/ops/capacity")
    assert capacity_resp.status_code == 200
    capacity_payload = capacity_resp.json()
    assert "recommendations" in capacity_payload

    slo_upsert = client.post(
        "/ops/observability/slos",
        json={
            "name": "api_availability",
            "metric": "ingest.p95_ms",
            "threshold": 3000,
            "comparator": "<",
            "window_minutes": 5,
        },
        headers=admin_headers,
    )
    assert slo_upsert.status_code == 200

    obs_refresh = client.get("/ops/observability")
    names = {slo["name"] for slo in obs_refresh.json().get("slos", [])}
    assert "api_availability" in names

    slo_delete = client.delete("/ops/observability/slos/api_availability", headers=admin_headers)
    assert slo_delete.status_code == 200
    assert slo_delete.json()["deleted"] is True

    dash_resp = client.post(
        "/ops/observability/dashboards",
        json={
            "dashboard_id": "custom_ops",
            "definition": {"title": "Custom Ops", "widgets": []},
        },
        headers=admin_headers,
    )
    assert dash_resp.status_code == 200
    assert dash_resp.json()["dashboard_id"] == "custom_ops"

    dash_delete = client.delete(
        "/ops/observability/dashboards/custom_ops",
        headers=admin_headers,
    )
    assert dash_delete.status_code == 200
    assert dash_delete.json()["deleted"] is True


def test_ops_mutations_require_admin(client: TestClient) -> None:
    runtime = api_server.runtime
    runtime.controller.add_node("node1")

    resp = client.post(
        "/ops/observability/slos",
        json={
            "name": "blocked",
            "metric": "ingest.p95_ms",
            "threshold": 4000,
            "comparator": ">",
            "window_minutes": 5,
        },
    )
    assert resp.status_code == 403

    dash_resp = client.post(
        "/ops/observability/dashboards",
        json={"dashboard_id": "blocked", "definition": {"title": "X", "widgets": []}},
    )
    assert dash_resp.status_code == 403
