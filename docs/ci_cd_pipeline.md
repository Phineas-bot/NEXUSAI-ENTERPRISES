# CI/CD Pipeline Blueprint

The pipeline below turns Section 7 requirements into actionable stages. Use this document to implement GitHub Actions, Azure Pipelines, or any preferred runner.

## Stage Overview

1. **Lint & Format**
   - Tools: `ruff`, `black`, `mypy`, `markdownlint`.
   - Command template:
     ```powershell
     python -m pip install -r requirements-dev.txt
     ruff check .
     black --check .
     mypy cloud_drive CloudSim
     markdownlint "**/*.md"
     ```
2. **Unit + Integration Tests**
   - Run PyTest with coverage gate (≥85%).
   - Command: `python -m pytest --cov=cloud_drive --cov=CloudSim tests/`.
3. **Build Artifacts**
   - Package Docker image or Python wheel. Example Docker build:
     ```powershell
     docker build -t nexusai/cloudsim:${{ github.sha }} .
     ```
4. **Deploy to Staging**
   - Trigger GitOps (e.g., ArgoCD) or run helm upgrade using manifests described in `docs/staging_plan.md`.
   - After deploy, execute smoke tests (REST/gRPC endpoints, SLO dashboards).
5. **Canary Promotion**
   - Use Argo Rollouts/Flagger to ramp traffic: 10% → 25% → 50% with automatic halt on SLO breach.
   - Feature flags (LaunchDarkly/OpenFeature) guard new behaviors; maintain per-env configuration.
6. **Full Production**
   - After canary stable for ≥30 minutes, complete rollout and archive metrics snapshot.

## Sample GitHub Actions Skeleton

```
.github/workflows/ci.yml
```

```yaml
name: CI
on:
  push:
    branches: [ main, "release/*" ]
  pull_request:

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements-dev.txt
      - name: Lint & Type Check
        run: |
          ruff check .
          black --check .
          mypy cloud_drive CloudSim
      - name: Run Tests
        run: |
          python -m pytest --cov=cloud_drive --cov=CloudSim tests/
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage.xml
          fail_ci_if_error: true
```

Adapt the workflow to your CI system; ensure secrets for staging/prod deploys live in the platform’s secure store.

## Safety Mechanisms

- **Rollback:** automated rollback triggers on error-rate spikes, SLO drift, or synthetic check failures. Manual ChatOps `!rollback <release>` command remains available.
- **Approvals:** require code-owner + SRE review for changes affecting persistence/auth/networking. Use branch protection to enforce review count and passing CI.
- **Security Gates:** dependency scanning (Snyk/Trivy), container image signing (cosign), and SBOM generation (Syft). Failing scans block promotion.
- **Database Migrations:** run Alembic migrations with `--sql` dry-run first, then apply in staging before production. Record rollback steps in PR description.

## Required Follow-Up

1. Add `requirements-dev.txt` enumerating dev/test dependencies.
2. Implement GitHub Actions (or equivalent) using the skeleton above.
3. Wire deployment stage to staging manifests and document secrets management.
