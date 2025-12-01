# Testing Matrix

This document operationalizes Section 7 of `full_cld.md` by mapping CloudSim feature areas to the required test coverage, tooling, and success criteria. Use it as the single source of truth when planning new work or validating readiness before a release.

## Coverage Targets

| Layer | Target | Scope | How to Measure |
| --- | --- | --- | --- |
| Unit | ≥ 85% line coverage on `cloud_drive` + core `CloudSim` libraries | Routing, manifests, auth helpers, policy engines, schedulers | `python -m pytest --cov=cloud_drive --cov=CloudSim` (coverage gate enforced in CI)
| Integration | Daily REST + gRPC suites; chunk router end-to-end | `tests/test_cloud_drive_rest.py`, `tests/test_cloud_drive_grpc.py`, `tests/test_storage_network.py` targeted flows | `pytest tests/test_cloud_drive_rest.py tests/test_cloud_drive_grpc.py` in CI + nightly cron run |
| Load/Performance | Validate up to 5K concurrent clients & 50 GB uploads | Resumable uploads, replica fan-out, manifest finalization | `load_tests/k6/resumable_upload.js` + `load_tests/locust/resiliency.py` feeding Grafana synthetic dashboards |
| Chaos/Resilience | Weekly runs in staging | Node/link failure, disk corruption, bus pauses, healing SLAs | Chaos suite harness (Gremlin/Fault injection scripts) with auto-report |

## Feature-to-Test Mapping

| Feature Area | Unit | Integration | Load | Chaos |
| --- | --- | --- | --- | --- |
| Upload sessions / manifests | ✅ `tests/test_cloud_drive_rest.py::TestUploads` | ✅ REST/gRPC suites | ✅ Multi-GB resumable script | ✅ Inject link/node failure during upload |
| Replica manager & healing | ✅ `tests/test_storage_network.py` | ✅ gRPC SLO RPCs | ✅ Replica fan-out soak | ✅ Disk corruption + rebuild |
| Auth / ACL checks | ✅ JWT parsing helpers | ✅ REST & gRPC auth tests | ⚠ Synthetic load with mixed scopes | ✅ Expire tokens mid-run |
| Observability pipeline | ✅ Metrics helpers | ✅ API e2e verifying dashboards | ⚠ Load ensures telemetry throughput | ✅ Chaos ensures alerts fire |

Legend: ✅ required, ⚠ recommended when feature changes.

## Tooling & Locations

- **Unit/Integration:** continue using PyTest. Add new tests beside feature modules with descriptive names. Update `tests/README.md` (future work) when new suites appear.
- **Load/Perf:** use the checked-in `load_tests/k6/resumable_upload.js` script for resumable sessions and `load_tests/locust/resiliency.py` for mixed REST traffic. Capture metrics (p95 latency, throughput, error budget) and push to Grafana dashboards identified in Section 6.
- **Chaos:** staging-only scripts (see `docs/staging_plan.md`). Automation should open issues when SLA breaches occur.

## Load Test Suite Contents

All artifacts referenced above now live under `load_tests/`:

- `README.md` – requirements, env vars, and quickstart commands for both runners.
- `k6/resumable_upload.js` – high-volume resumable upload scenario with chunk fan-out and commit verification.
- `locust/resiliency.py` – mixed read/write workload (listings, dashboards, resumable upload commits) with adjustable user counts.

Extend these scripts as new feature areas appear (e.g., search, advanced sharing) so the Section 7 exit criteria remain enforceable.

## Exit Criteria Before Release

1. All unit + integration tests green with ≥85% coverage.
2. Latest load-test report (≤7 days old) meets SLOs.
3. Latest chaos report (≤14 days) shows no open P0 regressions.
4. Matrix row for targeted feature is marked ✅ for all required layers.

Document owners: Dev Productivity + QA Guild. Update this file whenever the matrix changes.
