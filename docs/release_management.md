# Release Management Playbook

This playbook implements the release requirements from Section 7. Use it when planning any delivery of CloudSim or the associated control-plane services.

## Versioning Strategy

- Use **Semantic Versioning** (`MAJOR.MINOR.PATCH`) for REST/gRPC APIs and control-plane components.
- Append build metadata (e.g., `+sim`) for simulator-only changes that do not affect public APIs.
- Breaking API changes require a major version bump and a published deprecation schedule (minimum 90 days).

## Change Approval & Checklists

1. **Design Ready** – architecture notes merged into `full_cld.md` (or dedicated ADR) + testing matrix row updated.
2. **Code Review** – at least two approvals: owning squad + SRE/QA for persistence, networking, or auth changes.
3. **Change Advisory Board (CAB)** – required when touching database schema, encryption, or cross-region networking. CAB notes stored in `/docs/cab_logs/DATE.md` (create as needed).
4. **Pre-Release Checklist**
   - CI/CD green with coverage ≥85%.
   - Latest load + chaos test reports attached to release issue.
   - Staging verification: smoke tests, dashboards, alert dry-run.

## Release Cadence & Channels

- **Regular cadence:** every two weeks (Tuesday). Includes batched features + fixes.
- **Hotfix:** anytime for Sev-1/Sev-2 incidents; must follow post-mortem within 48h.
- **Beta channels:** opt-in cohorts per org/workspace toggled via feature flags. Beta exit criteria defined per feature (performance, usability, bug counts).

## Communication

- Generate release notes from conventional commits using `scripts/generate_release_notes.py` (future work).
- Publish notes to the docs portal and send summary email to mailing list (students/instructors).
- Update `README.md` badge/version to reflect the latest release.

## Post-Release Validation

- 24-hour heightened monitoring window with automated smoke tests every 15 minutes.
- Customer-support (or class TA) checklist: verify no spike in tickets, confirm telemetry dashboards healthy, ensure PagerDuty (if used) quiet.
- If any SLO violation occurs, pause further rollouts and schedule incident review.

## Rollback Procedures

1. Trigger CI/CD rollback job or use ChatOps command `!rollback <release-tag>`.
2. Revert database migrations via Alembic downgrade scripts.
3. Invalidate edge caches/CDN if API surface changed.
4. Communicate rollback status in the same channels as release notes.

## Artifacts & Records

- Maintain release issue template capturing scope, test evidence, approvals, and validation results.
- Store monitoring snapshots + chaos/load reports under `release_artifacts/<version>/` (directory to be created per release).
- Update Section 7 checklist to mark releases that met all criteria.
