# Load & Performance Tests

This folder hosts tooling referenced in `docs/testing_matrix.md`. The goal is to simulate high-volume resumable uploads/downloads plus resilience scenarios (replica fan-out, healing, etc.).

## Requirements

- Node.js 18+ (for k6 JavaScript bundles or `k6 browser` extensions).
- Python 3.11+ (for Locust workloads) with dependencies from `requirements-dev.txt` plus `locust` when needed.
- Environment variables:
  - `CLOUDSIM_REST_BASE` (e.g., `http://localhost:8000`)
  - `CLOUDSIM_GRPC_ADDR` (e.g., `localhost:50051`)
  - `AUTH_TOKEN` for authenticated API calls (shared-secret JWT during development).

## Quickstart

1. Install dev dependencies: `python -m pip install -r requirements-dev.txt`
2. (Optional) Install locust: `python -m pip install locust`
3. Run the k6 scenario:
   ```powershell
   k6 run k6/resumable_upload.js
   ```
4. Run the Locust scenario:
   ```powershell
   locust -f locust/resiliency.py --headless -u 500 -r 50 -t 10m
   ```

Metrics from these runs feed the dashboards described in Section 6. Keep scripts lightweight so they can run in CI if needed (smoke mode) and at scale in staging.
