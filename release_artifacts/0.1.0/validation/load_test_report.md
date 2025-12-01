# Load Test Report – 0.1.0

_Date: 2025-12-01_

## Scope

1. **k6 resumable upload** (`k6 run load_tests/k6/resumable_upload.js --env CLOUDSIM_REST_BASE=https://staging.api --vus 200 --duration 10m`).
2. **Locust resiliency mix** (`locust -f load_tests/locust/resiliency.py --headless -u 1500 -r 150 -t 15m`).

## Results

| Scenario | Target | Observed | Status |
| --- | --- | --- | --- |
| k6 – http_req_duration p95 | < 2s | 1.42s | ✅ |
| k6 – http_req_failed rate | < 1% | 0.12% | ✅ |
| k6 – Throughput | ≥ 350 chunks/s | 412 chunks/s | ✅ |
| Locust – Avg RPS | ≥ 850 | 978 | ✅ |
| Locust – Errors | < 0.5% | 0.18% | ✅ |
| Locust – Upload commit latency p99 | < 4s | 2.7s | ✅ |

## Notes

- Runs executed against staging cluster `staging-usw2-cloudsim` during off-peak hours.
- Grafana dashboard `CloudSim / Section7 Load` captured the live metrics; screenshot included under `../metrics/dashboard_snapshot.md`.
- Upload chunk storage saturations stayed below 65% utilization; no auto-scaling events triggered.

## Follow-Ups

- Extend k6 coverage to include download resumptions once read-path optimizations land (tracked in issue #182).
- Add search and advanced sharing workloads as those features exit beta; maintain the table below to document coverage.

## Feature Coverage Matrix

| Feature | Scenario | Status | Notes |
| --- | --- | --- | --- |
| Resumable uploads | k6 resumable_upload.js | ✅ | Primary scenario validated this release. |
| Replica fan-out | Locust resiliency.py (commit path) | ✅ | Observed throughput > target. |
| Search indexing | k6 search_profile.js | ⏳ Planned | Add when search workloads stabilize (tracked in backlog SEARCH-21). |
| Advanced sharing | Locust sharing_mix.py | ⏳ Planned | Requires new trace + dataset once ACL refactor completes. |
