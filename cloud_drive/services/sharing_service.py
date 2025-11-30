"""Sharing/ACL scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ..config import CloudDriveConfig
from ..models import FileEntry
from ..telemetry import TelemetryCollector
from .base import BaseService
from .metadata_service import MetadataService


@dataclass
class SharingService(BaseService):
    metadata_service: MetadataService
    _shares: Dict[str, List[str]] = None  # file_id -> principals

    def __post_init__(self) -> None:
        if self._shares is None:
            self._shares = {}

    def grant_access(self, file_id: str, principal: str) -> None:
        self._shares.setdefault(file_id, []).append(principal)
        self.emit_event("share_granted", file_id=file_id, principal=principal)

    def list_principals(self, file_id: str) -> List[str]:
        return self._shares.get(file_id, [])
