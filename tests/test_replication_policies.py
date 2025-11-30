from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cloud_drive.cloudsim import CloudSimController
from cloud_drive.config import CloudDriveConfig
from cloud_drive.messaging import InMemoryBus
from cloud_drive.services.healing_service import HealingService
from cloud_drive.services.lifecycle_service import LifecycleManager
from cloud_drive.services.metadata_service import MetadataService
from cloud_drive.services.replica_service import ReplicaManager
from cloud_drive.services.durability_service import DurabilityManager
from cloud_drive.models import ManifestSegment, FileManifest
from cloud_drive.telemetry import TelemetryCollector
from cloud_drive.services.manifest_utils import sim_manifest_to_model


def _bootstrap_services():
    cfg = CloudDriveConfig.default()
    telemetry = TelemetryCollector(cfg.observability)
    metadata = MetadataService(config=cfg, telemetry=telemetry)
    controller = CloudSimController()
    controller.add_node("node-a")
    controller.add_node("node-b")
    controller.add_node("node-c")
    controller.connect_nodes("node-a", "node-b", bandwidth_mbps=500, latency_ms=1.0)
    controller.connect_nodes("node-b", "node-c", bandwidth_mbps=500, latency_ms=1.5)
    replica_manager = ReplicaManager(config=cfg, telemetry=telemetry, controller=controller, metadata_service=metadata)
    lifecycle_manager = LifecycleManager(
        config=cfg,
        telemetry=telemetry,
        controller=controller,
        metadata_service=metadata,
        replica_manager=replica_manager,
    )
    durability_manager = DurabilityManager(
        config=cfg,
        telemetry=telemetry,
        controller=controller,
        metadata_service=metadata,
        replica_manager=replica_manager,
    )
    bus = InMemoryBus()
    healing = HealingService(
        config=cfg,
        telemetry=telemetry,
        controller=controller,
        metadata_service=metadata,
        replica_manager=replica_manager,
        lifecycle_manager=lifecycle_manager,
        durability_manager=durability_manager,
        bus=bus,
    )
    return cfg, controller, metadata, replica_manager, lifecycle_manager, healing, durability_manager


def test_lifecycle_demotes_idle_segments():
    cfg, controller, metadata, _, lifecycle_manager, _, _ = _bootstrap_services()
    manifest = FileManifest(
        manifest_id="manifest-1",
        file_id="file-1",
        total_size=cfg.storage.hot_cold_threshold_bytes + 1,
        segments=[
            ManifestSegment(node_id="node-a", file_id="chunk-1", offset=0, length=512, storage_tier="hot"),
            ManifestSegment(node_id="node-b", file_id="chunk-2", offset=512, length=512, storage_tier="hot"),
        ],
    )
    metadata.register_manifest(manifest)
    lifecycle_manager.record_access(manifest.manifest_id)
    lifecycle_manager._last_access[manifest.manifest_id] = datetime.now(timezone.utc) - timedelta(
        days=cfg.storage.lifecycle_policy.idle_days_before_cold + 1
    )
    transitioned = lifecycle_manager.evaluate_transitions()
    assert manifest.manifest_id in transitioned
    assert manifest.segments[0].storage_tier == cfg.storage.lifecycle_policy.hot_storage_tier
    assert manifest.segments[1].storage_tier == cfg.storage.lifecycle_policy.cold_storage_tier


def test_healing_collects_orphan_manifests():
    cfg, controller, metadata, replica_manager, lifecycle_manager, healing, _ = _bootstrap_services()
    result = controller.network.ingest_file("node-a", "healing.bin", 2048, prefer_local=True)
    assert result is not None
    _, transfer = result
    manifest_id = transfer.backing_file_id or transfer.file_id
    sim_manifest = controller.network.file_manifests_by_id[manifest_id]
    manifest_model = sim_manifest_to_model(sim_manifest)
    metadata.register_manifest(manifest_model)
    metadata._manifests.pop(manifest_model.manifest_id, None)
    orphans = healing.collect_orphans()
    assert manifest_model.manifest_id in orphans
    assert manifest_model.manifest_id not in controller.network.file_manifests_by_id


def test_durability_adds_parity_segments():
    cfg, _, metadata, _, _, _, durability = _bootstrap_services()
    policy = cfg.storage.durability_policy
    policy.enable_erasure_coding = True
    policy.erasure_parity_fragments = 1
    policy.erasure_min_object_bytes = 1024
    manifest = FileManifest(
        manifest_id="manifest-parity",
        file_id="file-parity",
        total_size=2048,
        segments=[
            ManifestSegment(node_id="node-a", file_id="chunk-a", offset=0, length=1024),
            ManifestSegment(node_id="node-b", file_id="chunk-b", offset=1024, length=1024),
        ],
    )
    metadata.register_manifest(manifest)
    updated = durability.ensure_erasure_coding(manifest)
    parity_segments = [seg for seg in updated.segments if seg.storage_tier == "parity"]
    assert parity_segments
    assert updated.durability is not None
    assert updated.durability.parity_fragments == len(parity_segments)


def test_durability_sets_encryption_metadata():
    cfg, _, metadata, _, _, _, durability = _bootstrap_services()
    cfg.storage.durability_policy.encryption_algorithm = "AES-256-GCM"
    manifest = FileManifest(
        manifest_id="manifest-enc",
        file_id="file-enc",
        total_size=128,
        segments=[
            ManifestSegment(node_id="node-a", file_id="chunk-a", offset=0, length=128),
        ],
    )
    metadata.register_manifest(manifest)
    updated = durability.ensure_encryption(manifest)
    assert updated.encryption is not None
    assert updated.encryption.algorithm == "AES-256-GCM"