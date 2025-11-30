"""Configuration primitives for the Cloud Drive control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DatabaseConfig:
    dsn: str
    pool_size: int = 10
    echo_sql: bool = False


@dataclass
class MessageBusConfig:
    backend: str = "in-memory"
    topics: List[str] = field(default_factory=lambda: [
        "ingest.requests",
        "replication.requests",
        "uploads.expired",
        "trash.expired",
        "activity.events",
        "quota.alert",
        "healing.events",
        "lifecycle.transitions",
    ])


@dataclass
class AuthConfig:
    oidc_issuer: str
    audience: str
    jwks_url: Optional[str] = None
    service_mesh_domain: str = "mesh.local"


@dataclass
class StorageFabricConfig:
    controller_endpoint: str = "local"
    default_chunk_size: int = 8 * 1024 * 1024
    max_chunk_size: int = 32 * 1024 * 1024
    hot_cold_threshold_bytes: int = 50 * 1024 * 1024
    replica_policy: "ReplicaPolicyConfig" | None = None
    lifecycle_policy: "LifecyclePolicyConfig" | None = None
    durability_policy: "DurabilityPolicyConfig" | None = None


@dataclass
class ReplicaPolicyConfig:
    hot_replicas: int = 2
    cold_replicas: int = 1
    min_unique_zones: int = 2
    spillover_threshold_bytes: int = 50 * 1024 * 1024


@dataclass
class LifecyclePolicyConfig:
    idle_days_before_cold: int = 30
    cold_storage_tier: str = "cold"
    hot_storage_tier: str = "hot"
    rebalance_interval_seconds: int = 3600


@dataclass
class DurabilityPolicyConfig:
    enable_checksums: bool = True
    enable_scrubbing: bool = True
    enable_erasure_coding: bool = False
    evacuation_storage_threshold: float = 0.9
    erasure_data_fragments: int = 8
    erasure_parity_fragments: int = 4
    erasure_min_object_bytes: int = 256 * 1024 * 1024
    encryption_algorithm: str = "AES-256-GCM"
    kms_key_id: str = "kms/default"


@dataclass
class ObservabilityConfig:
    metrics_endpoint: str = "http://localhost:9090"
    tracing_endpoint: str = "http://localhost:4317"
    log_level: str = "INFO"


@dataclass
class CloudDriveConfig:
    database: DatabaseConfig
    message_bus: MessageBusConfig
    auth: AuthConfig
    storage: StorageFabricConfig
    observability: ObservabilityConfig
    feature_flags: Dict[str, bool] = field(default_factory=dict)

    @staticmethod
    def default() -> "CloudDriveConfig":
        return CloudDriveConfig(
            database=DatabaseConfig(dsn="sqlite:///cloud_drive.db"),
            message_bus=MessageBusConfig(),
            auth=AuthConfig(oidc_issuer="https://example.okta.com", audience="cloud-drive"),
            storage=StorageFabricConfig(
                replica_policy=ReplicaPolicyConfig(),
                lifecycle_policy=LifecyclePolicyConfig(),
                durability_policy=DurabilityPolicyConfig(),
            ),
            observability=ObservabilityConfig(),
        )
