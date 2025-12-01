"""Data models shared across control-plane services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class Org:
    id: str
    name: str
    plan: str
    created_at: datetime


@dataclass
class User:
    id: str
    org_id: str
    email: str
    role: str
    quota_bytes: int
    created_at: datetime


@dataclass
class FileEntry:
    id: str
    org_id: str
    parent_id: Optional[str]
    name: str
    mime_type: str
    size_bytes: int
    checksum: Optional[str]
    is_folder: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None
    labels: List[str] = field(default_factory=list)


@dataclass
class EncryptionEnvelope:
    algorithm: str
    kek_id: str
    dek_id: str
    last_rotated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DurabilityMetadata:
    data_fragments: int
    parity_fragments: int
    checksum_algorithm: Optional[str]
    encryption_algorithm: Optional[str]


@dataclass
class ManifestSegment:
    node_id: str
    file_id: str
    offset: int
    length: int
    checksum: Optional[str] = None
    storage_tier: str = "hot"
    zone: Optional[str] = None
    encrypted: bool = True


@dataclass
class FileManifest:
    manifest_id: str
    file_id: str
    total_size: int
    segments: List[ManifestSegment] = field(default_factory=list)
    encryption: Optional[EncryptionEnvelope] = None
    durability: Optional[DurabilityMetadata] = None


@dataclass
class FileVersion:
    version_id: str
    file_id: str
    manifest_id: str
    version_number: int
    created_by: str
    created_at: datetime
    size_bytes: int
    parent_version_id: Optional[str] = None
    change_summary: Optional[str] = None
    autosave: bool = False
    is_pinned: bool = False
    label: Optional[str] = None


@dataclass
class ChunkStatus:
    chunk_id: int
    offset: int
    length: int
    checksum: Optional[str] = None
    status: str = "pending"
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class UploadSession:
    session_id: str
    file_id: Optional[str]
    org_id: str
    parent_id: str
    expected_size: int
    chunk_size: int
    created_by: str
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    received_bytes: int = 0
    file_name: Optional[str] = None
    source_node: Optional[str] = None
    manifest_id: Optional[str] = None
    max_parallel_streams: int = 1
    chunks: Dict[int, ChunkStatus] = field(default_factory=dict)
    status: str = "open"
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    client_hints: Dict[str, str] = field(default_factory=dict)


@dataclass
class ShareGrant:
    share_id: str
    file_id: str
    principal_type: str
    principal_id: str
    permission: str
    created_by: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    link_token: Optional[str] = None
    password_hash: Optional[str] = None


@dataclass
class SearchDocument:
    file_id: str
    org_id: str
    name: str
    labels: List[str]
    owners: List[str]
    principals: List[str]
    mime_type: str
    size_bytes: int
    updated_at: datetime


@dataclass
class ObservabilityEvent:
    event_type: str
    message: str
    attributes: Optional[dict] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
