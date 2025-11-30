"""Lifecycle management for hot/cold tiering and spillover policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, TYPE_CHECKING

from ..models import FileManifest
from ..cloudsim import CloudSimController
from ..messaging import InMemoryBus, MessageEnvelope
from .base import BaseService
from .metadata_service import MetadataService

if TYPE_CHECKING:  # pragma: no cover
    from .replica_service import ReplicaManager


@dataclass
class LifecycleManager(BaseService):
    controller: CloudSimController
    metadata_service: MetadataService
    replica_manager: "ReplicaManager" | None = None
    bus: InMemoryBus | None = None
    _last_access: Dict[str, datetime] = field(default_factory=dict)
    _last_rebalance_at: datetime | None = None

    def apply_post_upload(self, manifest: FileManifest, *, accessed_by: Optional[str] = None) -> FileManifest:
        self._record_access_time(manifest.manifest_id)
        policy = self.config.storage.lifecycle_policy
        if policy is None:
            return manifest
        self._annotate_zones(manifest)
        threshold = self.config.storage.hot_cold_threshold_bytes
        if manifest.total_size >= threshold:
            manifest = self._demote_tail_segments(manifest, policy.cold_storage_tier)
        return manifest

    def record_access(self, manifest_id: str) -> None:
        self._record_access_time(manifest_id)

    def evaluate_transitions(self) -> List[str]:
        policy = self.config.storage.lifecycle_policy
        if policy is None:
            return []
        now = datetime.now(timezone.utc)
        interval = getattr(policy, "rebalance_interval_seconds", 0)
        if interval and self._last_rebalance_at:
            delta = (now - self._last_rebalance_at).total_seconds()
            if delta < interval:
                return []
        self._last_rebalance_at = now
        cutoff = now - timedelta(days=policy.idle_days_before_cold)
        transitioned: List[str] = []
        for manifest in self.metadata_service.list_manifests():
            last_access = self._last_access.get(manifest.manifest_id)
            if last_access and last_access >= cutoff:
                continue
            self._annotate_zones(manifest)
            manifest = self._demote_tail_segments(manifest, policy.cold_storage_tier)
            self.metadata_service.upsert_manifest(manifest)
            transitioned.append(manifest.manifest_id)
        if transitioned:
            self.emit_event("lifecycle_transitions", manifest_ids=",".join(transitioned))
            if self.bus:
                self.bus.publish(
                    MessageEnvelope(
                        topic="lifecycle.transitions",
                        payload={"manifests": transitioned},
                    )
                )
        return transitioned

    def _demote_tail_segments(self, manifest: FileManifest, cold_tier: str) -> FileManifest:
        policy = self.config.storage.lifecycle_policy
        hot_tier = policy.hot_storage_tier if policy else "hot"
        tiered = False
        ordered = sorted(manifest.segments, key=lambda seg: seg.offset)
        for index, segment in enumerate(ordered):
            target_tier = hot_tier if index == 0 else cold_tier
            if segment.storage_tier != target_tier:
                segment.storage_tier = target_tier
                tiered = True
        if tiered and self.replica_manager:
            manifest = self.replica_manager.enforce_policy(manifest)
        return manifest

    def _annotate_zones(self, manifest: FileManifest) -> None:
        for segment in manifest.segments:
            node = self.controller.network.nodes.get(segment.node_id)
            zone = getattr(node, "zone", None)
            if zone:
                segment.zone = zone

    def _record_access_time(self, manifest_id: str) -> None:
        self._last_access[manifest_id] = datetime.now(timezone.utc)
