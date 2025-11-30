"""Background healing, reconciliation, and garbage collection routines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Set, TYPE_CHECKING

from ..messaging import InMemoryBus, MessageEnvelope
from ..cloudsim import CloudSimController
from .base import BaseService
from .metadata_service import MetadataService
from .lifecycle_service import LifecycleManager

if TYPE_CHECKING:  # pragma: no cover
    from .replica_service import ReplicaManager
    from .durability_service import DurabilityManager


@dataclass
class HealingService(BaseService):
    controller: CloudSimController
    metadata_service: MetadataService
    replica_manager: "ReplicaManager"
    lifecycle_manager: LifecycleManager | None
    durability_manager: "DurabilityManager" | None
    bus: InMemoryBus

    def run_health_checks(self) -> Dict[str, List[str]]:
        results = {
            "reconciled": self.reconcile_manifests(),
            "checksums": self.scrub_checksums(),
            "evacuated": self.evacuate_failed_nodes(),
            "garbage_collected": self.collect_orphans(),
        }
        if any(results.values()):
            self.bus.publish(MessageEnvelope(topic="healing.events", payload=results))
        return results

    def reconcile_manifests(self) -> List[str]:
        reconciled: List[str] = []
        for manifest in self.metadata_service.list_manifests():
            sim_manifest = self.controller.network.file_manifests_by_id.get(manifest.manifest_id)
            if sim_manifest:
                continue
            repaired = self.replica_manager.repair_manifest(manifest.manifest_id)
            if repaired:
                if self.lifecycle_manager:
                    repaired = self.lifecycle_manager.apply_post_upload(repaired)
                if self.durability_manager:
                    repaired = self.durability_manager.apply(repaired)
                self.metadata_service.upsert_manifest(repaired)
                reconciled.append(manifest.manifest_id)
        return reconciled

    def scrub_checksums(self) -> List[str]:
        policy = self.config.storage.durability_policy
        if policy and not policy.enable_scrubbing:
            return []
        healed: List[str] = []
        failed_nodes = set(self.controller.network.failed_nodes)
        for manifest in self.metadata_service.list_manifests():
            if any(segment.node_id in failed_nodes for segment in manifest.segments):
                updated = self.replica_manager.enforce_policy(manifest)
                if self.lifecycle_manager:
                    updated = self.lifecycle_manager.apply_post_upload(updated)
                if self.durability_manager:
                    updated = self.durability_manager.apply(updated)
                self.metadata_service.upsert_manifest(updated)
                healed.append(manifest.manifest_id)
        return healed

    def evacuate_failed_nodes(self) -> List[str]:
        policy = self.config.storage.durability_policy
        storage_threshold = (policy.evacuation_storage_threshold if policy else 0.9)
        degraded: Set[str] = set(self.controller.network.failed_nodes)
        for node_id, telemetry in self.controller.network.node_telemetry.items():
            if telemetry.storage_ratio >= storage_threshold:
                degraded.add(node_id)
        evacuated: List[str] = []
        if not degraded:
            return evacuated
        for manifest in self.metadata_service.list_manifests():
            if not any(segment.node_id in degraded for segment in manifest.segments):
                continue
            updated = self.replica_manager.enforce_policy(manifest)
            if self.lifecycle_manager:
                updated = self.lifecycle_manager.apply_post_upload(updated)
            if self.durability_manager:
                updated = self.durability_manager.apply(updated)
            self.metadata_service.upsert_manifest(updated)
            evacuated.append(manifest.manifest_id)
        return evacuated

    def collect_orphans(self) -> List[str]:
        metadata_ids = {manifest.manifest_id for manifest in self.metadata_service.list_manifests()}
        orphans: List[str] = []
        network_map = dict(self.controller.network.file_manifests_by_id)
        for manifest_id, sim_manifest in network_map.items():
            if manifest_id in metadata_ids:
                continue
            orphans.append(manifest_id)
            self._purge_manifest(sim_manifest)
        return orphans

    def _purge_manifest(self, sim_manifest: Any) -> None:
        self.controller.network.file_manifests_by_id.pop(sim_manifest.master_id, None)
        self.controller.network.file_manifests.pop(getattr(sim_manifest, "file_name", ""), None)
        self.controller.network.file_names.pop(sim_manifest.master_id, None)
        for segment in getattr(sim_manifest, "segments", []):
            node = self.controller.network.nodes.get(segment.node_id)
            if node and hasattr(node, "disk"):
                try:
                    node.disk.delete_file(segment.file_id)
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
                node.stored_files.pop(segment.file_id, None)
            self.controller.network.segment_manifests.pop(segment.file_id, None)
            self.controller.network.file_names.pop(segment.file_id, None)
