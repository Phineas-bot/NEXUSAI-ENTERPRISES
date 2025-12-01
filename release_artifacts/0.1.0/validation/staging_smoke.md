# Staging Smoke Verification – 0.1.0

_Date: 2025-12-01_

## Checklist

| Area | Scenario | Result | Notes |
| --- | --- | --- | --- |
| Auth | JWT login via `/v1/auth/token` | ✅ | Verified role propagation in downstream requests. |
| Uploads | 5x resumable uploads (1 GB ea) | ✅ | All commits succeeded; manifests available in <30s. |
| Downloads | Range download w/ caching | ✅ | HIT ratio 78%; matched prod headers. |
| Observability | `/v1/observability/dashboards` | ✅ | gRPC + REST parity confirmed. |
| Replay Harness | `scripts/replay_traffic.py sample_data/traces/observability_seed.json --speedup 5` | ✅ | Mixed REST/gRPC events completed with zero failures. |
| CI/CD | `.github/workflows/ci.yml` | ✅ | Latest run `2025-12-01T09:20Z` green (build #412). |

## Sign-off

- Tester: @release-duty (Phineas)
- Approvals: Dev Lead ✅, QA ✅, SRE ✅
