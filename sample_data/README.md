# Sample Data Sets

These fixtures feed the seeding and replay scripts described in Section 7. They are intentionally small so tests can run quickly while still representing realistic document types.

Contents:

- `documents/` – Markdown briefs and summaries that simulate user-created content.
- `datasets/` – CSV exports for analytics/scenario testing.
- `traces/` – REST/gRPC trace snippets for `scripts/replay_traffic.py`.

Feel free to augment this directory with additional files; the seeding script discovers files recursively.
