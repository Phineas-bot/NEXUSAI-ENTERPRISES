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
2. Execute `python scripts/seed_demo_data.py --rest-base https://staging.api --grpc-addr staging.api:50051 --dataset-root sample_data` to:
  - Stream tiered datasets (KB docs → GB csv/video) from `sample_data/datasets` via resumable sessions.
  - Hydrate knowledge-base docs from `sample_data/documents` with ACL variations.
  - Seed observability dashboards + SLOs through the gRPC control plane (requires `CLOUDSIM_GRPC_ADDR`).
3. Schedule the seeder weekly (Task Scheduler/Cron) with refreshed sample data to rotate activity feeds and expire stale sessions. The repo ships a GitHub Action (`.github/workflows/staging_refresh.yml`) that can run twice weekly using organization secrets to satisfy this requirement automatically.

## Replay Harness

- Capture anonymized REST/gRPC traces or message-bus envelopes from production (redacted) into `replay_traces/DATE/*.json` (see `sample_data/traces/observability_seed.json` for schema).
- Use `python scripts/replay_traffic.py --trace replay_traces/2025-12-01/uploads.json --rest-base https://staging.api --grpc-addr staging.api:50051 --speedup 5` to re-emit traffic against staging.
- Harness must support:
  - Time compression (speedup factor) for stress tests.
  - Selective amplification (e.g., duplicate upload traffic for scale testing).
  - Metrics emission (success/failure, latency distribution) so dashboards mirror prod. The script already annotates responses and gRPC outcomes in stdout, making it easy to forward to parsing/metrics collectors.

## Isolation & Observability

- Allocate separate staging tenants per squad with dedicated API keys to prevent test interference.
- Mirror production dashboards and alerts but route notifications to Slack/email only (no PagerDuty pages).
- Capture chaos-test output (weekly) and attach to Section 7 release checklist.

## Next Steps

1. Automate scheduled runs of `seed_demo_data.py` + `replay_traffic.py` via CI or staging cronjobs.
2. Expand sample datasets/traces as new feature sets emerge (e.g., search, comments, advanced ACLs).
3. Store synthetic datasets + traces in a secure storage bucket accessible to CI and staging pipelines (current repo copy is for dev parity).
