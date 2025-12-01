"""Runtime wiring for the high-level cloud drive architecture."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .cloudsim import CloudSimController

from .config import CloudDriveConfig
from .messaging import build_bus, InMemoryBus
from .services.api_gateway import APIGateway
from .services.metadata_service import MetadataService
from .services.upload_service import UploadOrchestrator
from .services.activity_service import ActivityService
from .services.sharing_service import SharingService
from .services.search_service import SearchIndexService
from .services.observability_service import ObservabilityManager
from .services.backup_service import BackupManager
from .services.capacity_planner import CapacityPlanner
from .services.replica_service import ReplicaManager
from .services.lifecycle_service import LifecycleManager
from .services.healing_service import HealingService
from .services.durability_service import DurabilityManager
from .telemetry import TelemetryCollector


@dataclass
class CloudDriveRuntime:
    config: CloudDriveConfig
    controller: CloudSimController
    bus: InMemoryBus
    telemetry: TelemetryCollector
    api_gateway: APIGateway
    metadata_service: MetadataService
    upload_service: UploadOrchestrator
    replica_manager: ReplicaManager
    lifecycle_manager: LifecycleManager
    healing_service: HealingService
    durability_manager: DurabilityManager
    activity_service: ActivityService
    search_service: SearchIndexService
    observability_manager: ObservabilityManager
    backup_manager: BackupManager
    capacity_planner: CapacityPlanner
    last_metrics_snapshot: dict[str, float] = field(default_factory=dict)

    @classmethod
    def bootstrap(cls, config: Optional[CloudDriveConfig] = None) -> "CloudDriveRuntime":
        cfg = config or CloudDriveConfig.default()
        controller = CloudSimController()
        bus = build_bus(cfg.message_bus.backend)
        telemetry = TelemetryCollector(cfg.observability)

        metadata_service = MetadataService(config=cfg, telemetry=telemetry)
        replica_manager = ReplicaManager(
            config=cfg,
            telemetry=telemetry,
            controller=controller,
            metadata_service=metadata_service,
        )
        durability_manager = DurabilityManager(
            config=cfg,
            telemetry=telemetry,
            controller=controller,
            metadata_service=metadata_service,
            replica_manager=replica_manager,
        )
        lifecycle_manager = LifecycleManager(
            config=cfg,
            telemetry=telemetry,
            controller=controller,
            metadata_service=metadata_service,
            replica_manager=replica_manager,
            bus=bus,
        )
        upload_service = UploadOrchestrator(
            config=cfg,
            controller=controller,
            bus=bus,
            metadata_service=metadata_service,
            telemetry=telemetry,
            replica_manager=replica_manager,
            lifecycle_manager=lifecycle_manager,
            durability_manager=durability_manager,
        )
        sharing_service = SharingService(config=cfg, telemetry=telemetry, metadata_service=metadata_service)
        search_service = SearchIndexService(config=cfg, telemetry=telemetry)
        observability_manager = ObservabilityManager(config=cfg, telemetry=telemetry)
        observability_manager.bootstrap_defaults()
        backup_manager = BackupManager(config=cfg, telemetry=telemetry)
        capacity_planner = CapacityPlanner(config=cfg, telemetry=telemetry)
        activity_service = ActivityService(bus=bus, telemetry=telemetry)
        healing_service = HealingService(
            config=cfg,
            telemetry=telemetry,
            controller=controller,
            metadata_service=metadata_service,
            replica_manager=replica_manager,
            lifecycle_manager=lifecycle_manager,
            durability_manager=durability_manager,
            bus=bus,
        )

        api_gateway = APIGateway(
            metadata_service=metadata_service,
            upload_service=upload_service,
            sharing_service=sharing_service,
            search_service=search_service,
            activity_service=activity_service,
            lifecycle_manager=lifecycle_manager,
            observability_manager=observability_manager,
            backup_manager=backup_manager,
            capacity_planner=capacity_planner,
        )

        runtime = cls(
            config=cfg,
            controller=controller,
            bus=bus,
            telemetry=telemetry,
            api_gateway=api_gateway,
            metadata_service=metadata_service,
            upload_service=upload_service,
            replica_manager=replica_manager,
            lifecycle_manager=lifecycle_manager,
            healing_service=healing_service,
            durability_manager=durability_manager,
            activity_service=activity_service,
            search_service=search_service,
            observability_manager=observability_manager,
            backup_manager=backup_manager,
            capacity_planner=capacity_planner,
        )
        runtime.api_gateway.metrics_supplier = runtime.get_metrics_snapshot
        return runtime

    def run_background_jobs(self) -> None:
        self.lifecycle_manager.evaluate_transitions()
        self.healing_service.run_health_checks()
        retention_days = int(self.config.feature_flags.get("trash_retention_days", 30))
        self.api_gateway.purge_trash(retention_days)
        metrics_snapshot = self._collect_metrics()
        self.last_metrics_snapshot = metrics_snapshot
        self.observability_manager.evaluate_slos(metrics_snapshot)
        manifests = self.metadata_service.list_manifests()
        if manifests:
            manifest_count = len(manifests)
            size_bytes = sum(manifest.total_size for manifest in manifests)
            self.backup_manager.run_backup(manifest_count, size_bytes, self.metadata_service.snapshot_stats())
        self.capacity_planner.evaluate(metrics_snapshot)

    def get_metrics_snapshot(self) -> dict[str, float]:
        if not self.last_metrics_snapshot:
            self.last_metrics_snapshot = self._collect_metrics()
        return dict(self.last_metrics_snapshot)

    def _collect_metrics(self) -> dict[str, float]:
        return {
            "ingest.p95_ms": self._ingest_latency_p95(),
            "storage.utilization": self._storage_utilization_ratio(),
            "replication.queue_depth": self._replication_queue_depth(),
        }

    def _storage_utilization_ratio(self) -> float:
        stats_fn = getattr(getattr(self.controller, "network", None), "get_network_stats", None)
        if not callable(stats_fn):
            return 0.0
        stats = stats_fn()
        storage_percent = float(stats.get("storage_utilization", 0.0))
        return storage_percent / 100.0

    def _replication_queue_depth(self) -> float:
        network = getattr(self.controller, "network", None)
        operations = getattr(network, "transfer_operations", {}) or {}
        depth = 0
        for transfers in operations.values():
            for transfer in transfers.values():
                status = getattr(transfer, "status", None)
                name = getattr(status, "name", str(status)) if status is not None else "unknown"
                if name != "COMPLETED":
                    depth += 1
        return float(depth)

    def _ingest_latency_p95(self) -> float:
        samples = [metric.get("value", 0.0) for metric in self.telemetry.metrics if metric.get("name") == "ingest.latency_ms"]
        if not samples:
            return 0.0
        samples = sorted(float(value) for value in samples)
        index = max(0, math.ceil(0.95 * len(samples)) - 1)
        return samples[index]
