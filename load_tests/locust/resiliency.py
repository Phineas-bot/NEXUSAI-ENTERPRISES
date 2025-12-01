from locust import HttpUser, between, task
import os

REST_BASE = os.environ.get("CLOUDSIM_REST_BASE", "http://localhost:8000")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")


def _headers(extra=None):
    headers = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    if extra:
        headers.update(extra)
    return headers


class ResiliencyUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(3)
    def list_files(self):
        self.client.get("/v1/files", headers=_headers())

    @task(2)
    def fetch_dashboard(self):
        self.client.get("/v1/observability/dashboards", headers=_headers())

    @task(1)
    def create_small_upload(self):
        payload = {"parent_id": "root", "size_bytes": 512_000, "md5": "stub"}
        resp = self.client.post("/v1/uploads:sessions", json=payload, headers=_headers())
        if resp.status_code >= 400:
            return
        session_id = resp.json().get("session_id")
        self.client.patch(
            f"/v1/uploads/{session_id}:commit",
            json={"checksum": "stub"},
            headers=_headers(),
        )
