"""Compute checksum + metadata for a dashboard snapshot and optionally prep upload info."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record dashboard snapshot metadata")
    parser.add_argument("image", type=Path, help="Path to the PNG snapshot")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("release_artifacts/0.1.0/metrics"),
        help="Directory where metadata files will be written",
    )
    parser.add_argument(
        "--object-url",
        required=True,
        help="Remote object URL (e.g., gs://bucket/version/dashboard_snapshot.png)",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional note to include in the metadata file",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    if not args.image.exists():
        raise SystemExit(f"Snapshot not found: {args.image}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256(args.image)
    timestamp = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    sha_file = args.output_dir / "dashboard_snapshot.sha256"
    sha_file.write_text(f"{digest}  {args.image.name}\n")

    meta = {
        "object_url": args.object_url,
        "sha256": digest,
        "generated_at": timestamp,
        "notes": args.notes,
    }
    meta_file = args.output_dir / "dashboard_snapshot.meta.json"
    meta_file.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"Wrote {sha_file} and {meta_file}")
    print("Upload the PNG separately, e.g.: gsutil cp {} {}".format(args.image, args.object_url))


if __name__ == "__main__":
    main()
