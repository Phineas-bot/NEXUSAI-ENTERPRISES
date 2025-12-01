# CloudSim 0.1.0

_Date: 2025-12-01_

## Highlights

- Added observability SLO management endpoints to the replay + seed tooling so staging mirrors production dashboards.
- Introduced reproducible load/perf harness (`load_tests/`) and synthetic data bundles under `sample_data/`.
- Wired GitHub Actions CI with linting, typing, tests, and coverage upload to enforce the Section 7 release bar.
- Established release artifacts ledger (this directory) to store validation evidence and monitoring exports per version.

## Fixed / Improved

- Hardened resumable upload flows, ensuring chunk commit paths log structured telemetry and can be replayed.
- Updated documentation (`docs/testing_matrix.md`, `docs/staging_plan.md`, `docs/release_management.md`) to reflect the new tooling contracts.
- Expanded developer ergonomics with `requirements-dev.txt`, ensuring lint/test tools stay in lock-step across contributors.

## Validation Snapshot

- Unit/integration suites (`pytest tests/test_cloud_drive_rest.py tests/test_cloud_drive_grpc.py`) passing locally and in CI.
- k6 + Locust load scenarios executed (see validation reports).
- Chaos experiments (node loss + gRPC latency injection) completed with no breached SLOs.
