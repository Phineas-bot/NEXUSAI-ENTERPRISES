"""Runtime wiring for the high-level cloud drive architecture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .cloudsim import CloudSimController

from .config import CloudDriveConfig
from .messaging import build_bus, InMemoryBus
from .services.api_gateway import APIGateway
from .services.metadata_service import MetadataService
from .services.upload_service import UploadOrchestrator
from .services.activity_service import ActivityService
from .services.sharing_service import SharingService
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
            activity_service=activity_service,
            lifecycle_manager=lifecycle_manager,
        )

        return cls(
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
        )

    def run_background_jobs(self) -> None:
        self.lifecycle_manager.evaluate_transitions()
        self.healing_service.run_health_checks()
