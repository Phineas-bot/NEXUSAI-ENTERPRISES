# Release Artifacts

Each release stores validation evidence, load/chaos reports, and monitoring snapshots as referenced in `docs/release_management.md`.

## Layout

```
release_artifacts/
└── <version>/
    ├── CHANGELOG.md
    ├── validation/
    │   ├── load_test_report.md
    │   ├── chaos_test_report.md
    │   └── staging_smoke.md
    └── metrics/
        ├── dashboard_snapshot.md
        ├── dashboard_snapshot.meta.json
        ├── dashboard_snapshot.sha256
        └── dashboard_snapshot.png (stored in artifact bucket, referenced here)
```

Populate the `<version>` directory during the release process. A starter `0.1.0` folder is included as a template.
