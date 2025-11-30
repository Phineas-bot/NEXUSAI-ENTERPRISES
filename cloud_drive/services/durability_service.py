"""Durability-focused helpers (checksums, encryption envelopes, erasure coding)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from ..cloudsim import CloudSimController
from ..models import DurabilityMetadata, EncryptionEnvelope, FileManifest, ManifestSegment
from .base import BaseService
from .metadata_service import MetadataService

if TYPE_CHECKING:  # pragma: no cover
    from .replica_service import ReplicaManager


@dataclass
class DurabilityManager(BaseService):
    controller: CloudSimController
    metadata_service: MetadataService
    replica_manager: "ReplicaManager" | None = None

    def apply(self, manifest: FileManifest, *, actor: Optional[str] = None) -> FileManifest:
        manifest = self.ensure_checksums(manifest)
        manifest = self.ensure_encryption(manifest, actor=actor)
        manifest = self.ensure_erasure_coding(manifest)
        return manifest

    def ensure_checksums(self, manifest: FileManifest) -> FileManifest:
        policy = self.config.storage.durability_policy
        if not policy or not policy.enable_checksums:
            return manifest
        for segment in manifest.segments:
            if segment.checksum:
                continue
            segment.checksum = self._checksum_for_segment(segment)
        checksum_algorithm = "md5"
        if manifest.durability:
            manifest.durability.checksum_algorithm = checksum_algorithm
        else:
            manifest.durability = DurabilityMetadata(
                data_fragments=len([seg for seg in manifest.segments if seg.storage_tier != "parity"]),
                parity_fragments=len([seg for seg in manifest.segments if seg.storage_tier == "parity"]),
                checksum_algorithm=checksum_algorithm,
                encryption_algorithm=(manifest.encryption.algorithm if manifest.encryption else None),
            )
        return manifest

    def ensure_encryption(self, manifest: FileManifest, *, actor: Optional[str] = None) -> FileManifest:
        policy = self.config.storage.durability_policy
        if not policy or not policy.encryption_algorithm:
            return manifest
        if manifest.encryption:
            return manifest
        dek = secrets.token_hex(16)
        manifest.encryption = EncryptionEnvelope(
            algorithm=policy.encryption_algorithm,
            kek_id=policy.kms_key_id,
            dek_id=f"dek-{manifest.manifest_id}-{dek}",
        )
        return manifest

    def ensure_erasure_coding(self, manifest: FileManifest) -> FileManifest:
        policy = self.config.storage.durability_policy
        if not policy or not policy.enable_erasure_coding:
            return manifest
        if manifest.total_size < policy.erasure_min_object_bytes:
            return manifest
        existing_parity = len([seg for seg in manifest.segments if seg.storage_tier == "parity"])
        if existing_parity >= policy.erasure_parity_fragments:
            return manifest
        parity_needed = policy.erasure_parity_fragments - existing_parity
        parity_size = max(1, manifest.total_size // max(1, policy.erasure_data_fragments))
        current_nodes = {segment.node_id for segment in manifest.segments}
        for index in range(parity_needed):
            parity_node = self._select_parity_node(current_nodes)
            if parity_node is None:
                break
            file_name = f"ec-{manifest.file_id}-{uuid.uuid4().hex[:8]}"
            transfer = self.controller.store_file_locally(parity_node, file_name, parity_size)
            if not transfer:
                continue
            segment = ManifestSegment(
                node_id=parity_node,
                file_id=transfer.file_id,
                offset=manifest.total_size,
                length=parity_size,
                checksum=self._checksum_for_id(transfer.file_id),
                storage_tier="parity",
                zone=getattr(self.controller.network.nodes.get(parity_node), "zone", None),
                encrypted=True,
            )
            manifest.segments.append(segment)
            current_nodes.add(parity_node)
        manifest.durability = DurabilityMetadata(
            data_fragments=policy.erasure_data_fragments,
            parity_fragments=len([seg for seg in manifest.segments if seg.storage_tier == "parity"]),
            checksum_algorithm=(manifest.durability.checksum_algorithm if manifest.durability else None),
            encryption_algorithm=(manifest.encryption.algorithm if manifest.encryption else None),
        )
        return manifest

    def _select_parity_node(self, exclude: set[str]) -> Optional[str]:
        best_node = None
        best_capacity = -1
        exclude = set(exclude)
        failed = set(self.controller.network.failed_nodes)
        for node_id, node in self.controller.network.nodes.items():
            if node_id in exclude or node_id in failed:
                continue
            free_bytes = getattr(node, "free_storage", 0)
            if free_bytes > best_capacity and free_bytes > 0:
                best_capacity = free_bytes
                best_node = node_id
        return best_node

    @staticmethod
    def _checksum_for_segment(segment: ManifestSegment) -> str:
        payload = f"{segment.node_id}:{segment.file_id}:{segment.offset}:{segment.length}".encode()
        return hashlib.md5(payload).hexdigest()

    @staticmethod
    def _checksum_for_id(file_id: str) -> str:
        return hashlib.md5(file_id.encode()).hexdigest()
