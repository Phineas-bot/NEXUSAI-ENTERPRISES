# Chaos Test Report – 0.1.0

_Date: 2025-11-29_

## Experiments

| ID | Experiment | Tool | Blast Radius | Expected Outcome | Result |
| --- | --- | --- | --- | --- | --- |
| CH-01 | Terminate storage virtual node (`storage-az1-vn2`) | Gremlin shutdown | 1 replica in AZ1 | Healing completes < 5 min, no data loss | ✅ Rebuilt in 3m12s |
| CH-02 | Inject 250 ms latency on gRPC control-plane link | Istio fault injection | 100% of ObservabilityService traffic | Alert fires, retry budget < 5% | ✅ Alert triggered in 90s, retries 2.1% |
| CH-03 | Corrupt chunk manifest for active upload | Custom chaos script | Single upload session | Client surfaces retriable error, telemetry emits `chunk_corruption_detected` | ✅ Client retried automatically |

## Observations

- Alert routing confirmed: notifications delivered to `#cloudsim-staging` Slack channel only.
- Healing controller kept SLO within 99.5% availability; no customer-visible downtime simulated.
- Added countermeasure: enable proactive scrub job daily to catch manifest drift sooner (tracked in backlog item OPS-72).

## Attachments

- Detailed Gremlin runbook + logs stored alongside this repo at `release_artifacts/0.1.0/validation/chaos_logs/` (available in artifact storage bucket `gs://nexusai-release-artifacts/0.1.0`).
- Future feature areas to capture:

| Feature | Chaos hypothesis | Status | Notes |
| --- | --- | --- | --- |
| Search | Query node crash mid-flight | ⏳ Planned | Will be added once search rollout completes (SEARCH-35). |
| Advanced sharing | ACL propagation delay injection | ⏳ Planned | Dependent on new event bus instrumentation. |
| Notifications | Drop activity feed queue for 5 min | ⏳ Planned | Scoped for Q1 2026 release train. |
