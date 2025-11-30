"""Replica placement and healing logic for manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..config import CloudDriveConfig
from ..models import FileManifest, ManifestSegment
from ..telemetry import TelemetryCollector
from ..cloudsim import CloudSimController
from .base import BaseService
from .metadata_service import MetadataService
from .manifest_utils import sim_manifest_to_model


@dataclass
class ReplicaManager(BaseService):
    controller: CloudSimController
    metadata_service: MetadataService

    def enforce_policy(self, manifest: FileManifest) -> FileManifest:
        policy = self.config.storage.replica_policy
        if policy is None:
            return manifest

        required_copies = max(1, policy.hot_replicas + policy.cold_replicas)
        current_nodes = {segment.node_id for segment in manifest.segments}
        current_zones = self._zones_for_nodes(current_nodes)
        if len(current_nodes) >= required_copies:
            return manifest

        updated_manifest = manifest
        for _ in range(required_copies - len(current_nodes)):
            source_segment = self._pick_source_segment(updated_manifest.segments)
            if source_segment is None:
                break
            target_node = self._select_target_node(
                exclude=current_nodes,
                existing_zones=current_zones,
                required_bytes=source_segment.length,
                min_unique_zones=policy.min_unique_zones,
            )
            if target_node is None:
                break
            transfer = self.controller.network.initiate_replica_transfer(
                source_segment.node_id,
                target_node,
                source_segment.file_id,
            )
            if transfer is None:
                continue
            current_nodes.add(target_node)
            current_zones = self._zones_for_nodes(current_nodes)
            updated_manifest = self._refresh_manifest(updated_manifest.manifest_id)

        self.metadata_service.upsert_manifest(updated_manifest)
        return updated_manifest

    def repair_manifest(self, manifest_id: str) -> Optional[FileManifest]:
        sim_manifest = self.controller.network.file_manifests_by_id.get(manifest_id)
        if sim_manifest is None:
            return None
        manifest = sim_manifest_to_model(sim_manifest)
        self.metadata_service.upsert_manifest(manifest)
        return manifest

    def _refresh_manifest(self, manifest_id: str) -> FileManifest:
        sim_manifest = self.controller.network.file_manifests_by_id.get(manifest_id)
        if sim_manifest is None:
            raise RuntimeError(f"Manifest {manifest_id} missing from storage fabric")
        return sim_manifest_to_model(sim_manifest)

    def _select_target_node(
        self,
        *,
        exclude: Iterable[str],
        existing_zones: set[str],
        required_bytes: int,
        min_unique_zones: int,
    ) -> Optional[str]:
        exclude_set = set(exclude)
        preferred: list[str] = []
        fallbacks: list[str] = []
        for node_id, node in self.controller.network.nodes.items():
            if node_id in exclude_set or node_id in self.controller.network.failed_nodes:
                continue
            free_bytes = getattr(node, "free_storage", 0)
            if free_bytes < required_bytes:
                continue
            zone = getattr(node, "zone", None)
            if zone and zone not in existing_zones and len(existing_zones) < min_unique_zones:
                preferred.append(node_id)
            else:
                fallbacks.append(node_id)
        if preferred:
            return preferred[0]
        if fallbacks:
            return fallbacks[0]
        return None

    @staticmethod
    def _pick_source_segment(segments: Iterable[ManifestSegment]) -> Optional[ManifestSegment]:
        return next(iter(segments), None)

    def _zones_for_nodes(self, nodes: Iterable[str]) -> set[str]:
        zones: set[str] = set()
        for node_id in nodes:
            node = self.controller.network.nodes.get(node_id)
            zone = getattr(node, "zone", None)
            if zone:
                zones.add(zone)
        return zones
