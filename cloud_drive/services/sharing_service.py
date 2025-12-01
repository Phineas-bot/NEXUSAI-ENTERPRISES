"""Sharing/ACL scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
import hashlib
from typing import Dict, List, Optional

from ..config import CloudDriveConfig
from ..models import ShareGrant
from ..telemetry import TelemetryCollector
from .base import BaseService
from .metadata_service import MetadataService


@dataclass
class SharingService(BaseService):
    metadata_service: MetadataService
    _shares: Dict[str, List[ShareGrant]] = None  # file_id -> grants

    def __post_init__(self) -> None:
        if self._shares is None:
            self._shares = {}

    def grant_access(
        self,
        file_id: str,
        *,
        principal_type: str,
        principal_id: str,
        permission: str,
        created_by: str,
        expires_at: Optional[datetime] = None,
        link_token: Optional[str] = None,
        password: Optional[str] = None,
    ) -> ShareGrant:
        share = ShareGrant(
            share_id=str(uuid.uuid4()),
            file_id=file_id,
            principal_type=principal_type,
            principal_id=principal_id,
            permission=permission,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            link_token=link_token,
            password_hash=self._hash_password(password) if password else None,
        )
        self._shares.setdefault(file_id, []).append(share)
        self.emit_event("share_granted", file_id=file_id, share_id=share.share_id, permission=permission)
        return share

    def list_principals(self, file_id: str) -> List[str]:
        return [grant.principal_id for grant in self._shares.get(file_id, [])]

    def list_grants(self, file_id: str) -> List[ShareGrant]:
        return list(self._shares.get(file_id, []))

    def revoke_access(self, file_id: str, share_id: str) -> None:
        grants = self._shares.get(file_id, [])
        updated = [grant for grant in grants if grant.share_id != share_id]
        if len(updated) != len(grants):
            self._shares[file_id] = updated
            self.emit_event("share_revoked", file_id=file_id, share_id=share_id)

    def purge_file(self, file_id: str) -> None:
        if file_id in self._shares:
            self._shares.pop(file_id, None)

    @staticmethod
    def _hash_password(password: str) -> str:
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"
