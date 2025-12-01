# Dashboard Snapshot – 0.1.0

A full-resolution PNG export (`dashboard_snapshot.png`, 2560×1440) capturing the Grafana board *CloudSim / Section7 Load* was generated on 2025-12-01 at 09:45 UTC. The file is stored in the artifact bucket `gs://nexusai-release-artifacts/0.1.0/dashboard_snapshot.png` and referenced here for traceability.

Use `python scripts/publish_dashboard_snapshot.py <path-to-png> --object-url gs://... --output-dir release_artifacts/0.1.0/metrics` after exporting a new snapshot. The script writes `dashboard_snapshot.sha256` and `dashboard_snapshot.meta.json`, ensuring the repo always records the checksum + metadata matching the uploaded PNG.

Key callouts visible in the snapshot:

- Upload chunk latency p95 steady at 1.4 s (goal 2 s).
- Error budget burn reset to 3% after chaos tests.
- gRPC ObservabilityService success rate plateauing at 99.7%.

> Note: the PNG exceeds repo size guidelines, so only the metadata is tracked here. Pull from the bucket when assembling the final release packet.
