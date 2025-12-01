# Staging Environment & Replay Plan

This document explains how to stand up and keep parity between staging and production-like environments, as required by Section 7 of `full_cld.md`.

## Topology Expectations

- Minimum 3 availability zones (can be simulated via Kubernetes node labels or docker-compose subnets).
- Multi-region control plane: run two replicas of the API/observability stack with independent CloudSim runtimes; connect via the same gRPC/REST endpoints used in prod.
- Service mesh (Linkerd/Istio) must be enabled with mTLS to validate auth + tracing instrumentation before prod.
- Feature flags default **ON** in staging so behavior is validated prior to production rollout.

## Infrastructure Blueprint

```
staging/
├── k8s/
│   ├── namespace.yaml
│   ├── mesh-config.yaml
│   └── cloudsim-stack.yaml       # StatefulSets for CloudSim nodes + services
├── terraform/
│   └── networking.tf             # Optional IaaS provisioning (VPC, subnets)
└── scripts/
    └── bootstrap.ps1             # Convenience script to deploy manifests
```

(Apply these gradually; the repo currently hosts only application code, so infrastructure templates live in a separate ops repo. Keep this structure for reference.)

## Synthetic Data Seeding

Purpose: ensure staging constantly exercises ACLs, search, replication, and notifications.

1. Run `python -m CloudSim.main --scenario hotspot` to warm the topology.
2. Execute `python scripts/seed_demo_data.py --env staging` (script to be authored) to:
   - Create multiple orgs/users with varied roles.
   - Upload tiered datasets (KB docs → 10+ GB media) using resumable sessions.
   - Apply sharing permissions, comments, and activity feed events.
3. Schedule the seeder weekly (Task Scheduler/Cron) to refresh datasets and expire stale sessions.

## Replay Harness

- Capture anonymized REST/gRPC traces or message-bus envelopes from production (redacted) into `replay_traces/DATE/*.json`.
- Use `python scripts/replay_traffic.py --trace replay_traces/2025-12-01/uploads.json --speedup 5` to re-emit traffic against staging.
- Harness must support:
  - Time compression (speedup factor) for stress tests.
  - Selective amplification (e.g., duplicate upload traffic for scale testing).
  - Metrics emission (success/failure, latency distribution) so dashboards mirror prod.

Scripts are not yet implemented; this plan documents the contract so engineering tasks can be tracked.

## Isolation & Observability

- Allocate separate staging tenants per squad with dedicated API keys to prevent test interference.
- Mirror production dashboards and alerts but route notifications to Slack/email only (no PagerDuty pages).
- Capture chaos-test output (weekly) and attach to Section 7 release checklist.

## Next Steps

1. Implement `scripts/seed_demo_data.py` (Python) leveraging existing CloudSim APIs.
2. Build `scripts/replay_traffic.py` with support for REST + gRPC payloads.
3. Store synthetic datasets + traces in a secure storage bucket accessible to CI and staging pipelines.
