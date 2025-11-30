"""Shared helpers for translating CloudSim manifests into control-plane models."""

from __future__ import annotations

from typing import Any, Iterable

from ..models import FileManifest, ManifestSegment


def to_manifest_segments(sim_segments: Iterable[Any]) -> list[ManifestSegment]:
    segments: list[ManifestSegment] = []
    for segment in sim_segments:
        segments.append(
            ManifestSegment(
                node_id=getattr(segment, "node_id"),
                file_id=getattr(segment, "file_id"),
                offset=getattr(segment, "offset", 0),
                length=getattr(segment, "size", getattr(segment, "length", 0)),
                checksum=getattr(segment, "checksum", None),
                storage_tier=getattr(segment, "storage_tier", "hot"),
                zone=getattr(segment, "zone", None),
                encrypted=getattr(segment, "encrypted", True),
            )
        )
    segments.sort(key=lambda seg: seg.offset)
    return segments


def sim_manifest_to_model(sim_manifest: Any) -> FileManifest:
    return FileManifest(
        manifest_id=getattr(sim_manifest, "master_id", getattr(sim_manifest, "manifest_id", "")),
        file_id=getattr(sim_manifest, "master_id", getattr(sim_manifest, "file_id", "")),
        total_size=getattr(sim_manifest, "total_size", 0),
        segments=to_manifest_segments(getattr(sim_manifest, "segments", [])),
    )
