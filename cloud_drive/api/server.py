"""FastAPI-based gateway for external clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..runtime import CloudDriveRuntime
from .grpc_server import build_grpc_server, configure_grpc_listener


runtime = CloudDriveRuntime.bootstrap()
_grpc_server = None
_grpc_bind = os.environ.get("CLOUD_DRIVE_GRPC_BIND", "0.0.0.0:50051")
_grpc_tls_cert = os.environ.get("CLOUD_DRIVE_GRPC_TLS_CERT")
_grpc_tls_key = os.environ.get("CLOUD_DRIVE_GRPC_TLS_KEY")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _grpc_server
    if _grpc_server is None:
        server = build_grpc_server(runtime)
        try:
            port = configure_grpc_listener(
                server,
                _grpc_bind,
                tls_cert_path=_grpc_tls_cert,
                tls_key_path=_grpc_tls_key,
            )
        except (ValueError, RuntimeError) as exc:  # pragma: no cover - startup misconfiguration
            logger.error("Unable to start gRPC server: %s", exc)
            raise
        server.start()
        tls_enabled = bool(_grpc_tls_cert and _grpc_tls_key)
        logger.info(
            "gRPC control-plane server listening on %s (port=%s, tls=%s)",
            _grpc_bind,
            port,
            tls_enabled,
        )
        _grpc_server = server
    try:
        yield
    finally:
        if _grpc_server:
            _grpc_server.stop(0)
            logger.info("gRPC control-plane server stopped")
        _grpc_server = None


app = FastAPI(title="Cloud Drive API", version="0.1.0", lifespan=_lifespan)


class AuthContext(BaseModel):
    user_id: str
    org_id: str
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)

    @property
    def is_ops_admin(self) -> bool:
        allowed = _ops_admin_roles()
        claims = {role.lower() for role in (self.roles + self.scopes)}
        return bool(claims.intersection(allowed))


async def get_auth_context(request: Request) -> AuthContext:
    """Parses JWT or development headers to build auth context."""
    shared_secret = runtime.config.auth.shared_secret
    auth_header = request.headers.get("authorization")
    jwt_payload: Optional[dict] = None
    if shared_secret and auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            jwt_payload = _decode_jwt(token, shared_secret)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    user_id = request.headers.get("x-user-id", "user-123")
    org_id = request.headers.get("x-org-id", "org-123")
    roles = _split_claim_header(request.headers.get("x-user-roles"))
    scopes = _split_claim_header(request.headers.get("x-user-scopes"))

    if jwt_payload:
        user_id = str(jwt_payload.get("sub", user_id))
        org_id = str(jwt_payload.get("org_id", org_id))
        token_roles = jwt_payload.get("roles") or jwt_payload.get("groups") or []
        roles = list(dict.fromkeys(_ensure_list(token_roles) + roles))
        token_scopes = jwt_payload.get("scp") or jwt_payload.get("scopes") or []
        scopes = list(dict.fromkeys(_ensure_list(token_scopes) + scopes))

    return AuthContext(user_id=user_id, org_id=org_id, roles=roles, scopes=scopes)


def _ensure_ops_admin(ctx: AuthContext) -> None:
    if not ctx.is_ops_admin:
        raise HTTPException(status_code=403, detail="ops.admin role required")


def _ops_admin_roles() -> set[str]:
    roles = getattr(runtime.config.auth, "ops_admin_roles", None) or ["ops.admin"]
    return {role.lower() for role in roles}


def _split_claim_header(value: Optional[str]) -> list[str]:
    if not value:
        return []
    tokens = []
    for part in value.replace(",", " ").split():
        part = part.strip()
        if part:
            tokens.append(part)
    return tokens


def _ensure_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if isinstance(value, str):
        return _split_claim_header(value)
    if value:
        return [str(value)]
    return []


def _decode_jwt(token: str, secret: str) -> dict:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise ValueError("Malformed JWT") from exc
    header = _b64url_to_json(header_b64)
    if header.get("alg") != "HS256":
        raise ValueError("Unsupported JWT alg")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    actual = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected, actual):
        raise ValueError("Invalid JWT signature")
    return _b64url_to_json(payload_b64)


def _b64url_to_json(segment: str) -> dict:
    data = _b64url_decode(segment)
    return json.loads(data.decode())


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


class FolderCreateRequest(BaseModel):
    name: str
    parent_id: Optional[str] = Field(default=None)


class UploadInitRequest(BaseModel):
    file_id: Optional[str] = None
    parent_id: str
    size_bytes: int
    chunk_size: Optional[int] = None
    network_type: Optional[str] = Field(default=None)
    device_class: Optional[str] = Field(default=None)
    max_parallel_streams: Optional[int] = Field(default=None, ge=1, le=16)
    client_metadata: Optional[dict[str, str]] = Field(default=None)


class UploadChunkRequest(BaseModel):
    session_id: str
    source_node: str
    file_name: str
    chunk_bytes: int
    chunk_id: Optional[int] = None
    offset: Optional[int] = None
    checksum: Optional[str] = None


class ShareRequest(BaseModel):
    principal: str
    principal_type: str = Field(default="user")
    permission: str = Field(default="viewer")
    expires_at: Optional[datetime] = None
    password: Optional[str] = None
    link_token: Optional[str] = None


class RestoreRequest(BaseModel):
    parent_id: Optional[str] = None


class VersionLabelRequest(BaseModel):
    label: Optional[str] = None
    is_pinned: Optional[bool] = None
    autosave: Optional[bool] = None
    change_summary: Optional[str] = None


class SLOUpsertRequest(BaseModel):
    name: str
    metric: str
    threshold: float
    comparator: str = Field(default=">=")
    window_minutes: int = Field(default=15, ge=1)


class DashboardUpsertRequest(BaseModel):
    dashboard_id: str
    definition: dict[str, Any]


@app.post("/folders")
async def create_folder(payload: FolderCreateRequest, ctx: AuthContext = Depends(get_auth_context)):
    folder = runtime.api_gateway.create_folder(ctx.org_id, payload.parent_id, payload.name, ctx.user_id)
    return _serialize_file(folder)


@app.post("/uploads:sessions")
async def initiate_upload(payload: UploadInitRequest, ctx: AuthContext = Depends(get_auth_context)):
    hints = {k: v for k, v in {
        "network_type": payload.network_type,
        "device_class": payload.device_class,
    }.items() if v}
    if payload.client_metadata:
        hints.update(payload.client_metadata)
    client_hints = hints or None
    session = runtime.api_gateway.start_upload(
        ctx.org_id,
        payload.parent_id,
        payload.size_bytes,
        ctx.user_id,
        file_id=payload.file_id,
        chunk_size=payload.chunk_size,
        client_hints=client_hints,
        max_parallel_streams=payload.max_parallel_streams,
    )
    summary = runtime.api_gateway.describe_upload(session.session_id)
    return summary


@app.post("/uploads:chunk")
async def append_chunk(payload: UploadChunkRequest):
    try:
        runtime.api_gateway.append_chunk(
            payload.session_id,
            payload.source_node,
            payload.file_name,
            payload.chunk_bytes,
            chunk_id=payload.chunk_id,
            offset=payload.offset,
            checksum=payload.checksum,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    summary = runtime.api_gateway.describe_upload(payload.session_id)
    return {"status": "queued", "received_bytes": summary["received_bytes"], "gap_map": summary["gap_map"]}


@app.get("/uploads:sessions/{session_id}")
async def get_upload_status(session_id: str):
    try:
        summary = runtime.api_gateway.describe_upload(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return summary


@app.post("/uploads:finalize/{session_id}")
async def finalize_upload(session_id: str):
    try:
        session = runtime.api_gateway.get_upload_session(session_id)
    except KeyError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        manifest = runtime.api_gateway.finalize_upload(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    chunk_size = session.chunk_size or 1
    total_chunks = (session.expected_size + chunk_size - 1) // chunk_size
    completed_chunks = session.received_bytes // chunk_size
    metadata = {
        "resource_id": session.file_id,
        "manifest_id": manifest.manifest_id if manifest else session.manifest_id,
        "total_chunks": total_chunks,
        "completed_chunks": completed_chunks,
    }
    operation = {
        "operation_id": f"uploads/{session_id}:finalize",
        "done": True,
        "metadata": metadata,
    }
    return {"operation": operation}


@app.get("/files/{file_id}/download")
async def download_file(
    file_id: str,
    offset: int = 0,
    length: Optional[int] = None,
    chunk_size: Optional[int] = None,
):
    try:
        stream = runtime.api_gateway.stream_download(
            file_id,
            offset=offset,
            length=length,
            chunk_size=chunk_size,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def iterator():
        for _, blob, _ in stream:
            yield blob

    return StreamingResponse(iterator(), media_type="application/octet-stream")


@app.post("/files/{file_id}:share")
async def share_file(file_id: str, payload: ShareRequest, ctx: AuthContext = Depends(get_auth_context)):
    principal_type = payload.principal_type or "user"
    link_token = payload.link_token
    if principal_type == "public" and not link_token:
        link_token = uuid.uuid4().hex
    share = runtime.api_gateway.grant_share(
        file_id,
        principal_type=principal_type,
        principal_id=payload.principal,
        permission=payload.permission or "viewer",
        created_by=ctx.user_id,
        expires_at=payload.expires_at,
        link_token=link_token,
        password=payload.password,
    )
    return _serialize_share(share)


@app.get("/files/{file_id}/shares")
async def list_shares(file_id: str):
    return [_serialize_share(share) for share in runtime.api_gateway.list_shares(file_id)]


@app.delete("/files/{file_id}/shares/{share_id}")
async def revoke_share(file_id: str, share_id: str):
    runtime.api_gateway.revoke_share(file_id, share_id)
    return {"status": "revoked", "share_id": share_id}


@app.get("/activity")
async def list_activity():
    return runtime.api_gateway.list_activity()


@app.get("/ops/observability")
async def get_observability_overview():
    return runtime.api_gateway.get_observability_overview()


@app.get("/ops/backups")
async def list_backup_snapshots():
    return runtime.api_gateway.list_backups()


@app.get("/ops/capacity")
async def get_capacity_overview():
    return runtime.api_gateway.get_capacity_overview()


@app.post("/ops/observability/slos")
async def upsert_slo(payload: SLOUpsertRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    try:
        return runtime.api_gateway.upsert_slo(
            name=payload.name,
            metric=payload.metric,
            threshold=payload.threshold,
            comparator=payload.comparator,
            window_minutes=payload.window_minutes,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/ops/observability/slos/{name}")
async def delete_slo(name: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    try:
        return runtime.api_gateway.delete_slo(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/ops/observability/dashboards")
async def upsert_dashboard(payload: DashboardUpsertRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    try:
        return runtime.api_gateway.upsert_dashboard(payload.dashboard_id, payload.definition)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/ops/observability/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    try:
        return runtime.api_gateway.delete_dashboard(dashboard_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/files/{file_id}")
async def trash_file(file_id: str, ctx: AuthContext = Depends(get_auth_context)):
    try:
        entry = runtime.api_gateway.trash_file(file_id, ctx.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "trashed", "file": _serialize_file(entry)}


@app.get("/trash")
async def list_trash(ctx: AuthContext = Depends(get_auth_context)):
    trashed = runtime.api_gateway.list_trashed(org_id=ctx.org_id)
    return [_serialize_file(entry) for entry in trashed]


@app.post("/trash/{file_id}:restore")
async def restore_file(file_id: str, payload: RestoreRequest, ctx: AuthContext = Depends(get_auth_context)):
    try:
        entry = runtime.api_gateway.restore_file(file_id, ctx.user_id, parent_id=payload.parent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_file(entry)


def _serialize_file(entry) -> dict:
    return {
        "id": entry.id,
        "org_id": entry.org_id,
        "parent_id": entry.parent_id,
        "name": entry.name,
        "mime_type": entry.mime_type,
        "size_bytes": entry.size_bytes,
        "checksum": entry.checksum,
        "is_folder": entry.is_folder,
        "created_by": entry.created_by,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
        "deleted_at": entry.deleted_at.isoformat() if entry.deleted_at else None,
        "deleted_by": entry.deleted_by,
        "labels": entry.labels,
    }


def _serialize_share(share) -> dict:
    return {
        "share_id": share.share_id,
        "file_id": share.file_id,
        "principal_type": share.principal_type,
        "principal_id": share.principal_id,
        "permission": share.permission,
        "created_by": share.created_by,
        "created_at": share.created_at.isoformat(),
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "link_token": share.link_token,
    }


def _serialize_version(version) -> dict:
    return {
        "version_id": version.version_id,
        "file_id": version.file_id,
        "manifest_id": version.manifest_id,
        "version_number": version.version_number,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat(),
        "size_bytes": version.size_bytes,
        "parent_version_id": version.parent_version_id,
        "change_summary": version.change_summary,
        "autosave": version.autosave,
        "is_pinned": version.is_pinned,
        "label": version.label,
    }


def _serialize_search(doc) -> dict:
    return {
        "file_id": doc.file_id,
        "org_id": doc.org_id,
        "name": doc.name,
        "labels": doc.labels,
        "owners": doc.owners,
        "principals": doc.principals,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "updated_at": doc.updated_at.isoformat(),
    }


@app.get("/files/{file_id}/versions")
async def list_versions(file_id: str):
    versions = runtime.api_gateway.list_versions(file_id)
    return [_serialize_version(v) for v in versions]


@app.post("/files/{file_id}/versions/{version_id}:restore")
async def restore_version(file_id: str, version_id: str, ctx: AuthContext = Depends(get_auth_context)):
    try:
        restored = runtime.api_gateway.restore_version(file_id, version_id, ctx.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_version(restored)


@app.post("/files/{file_id}/versions/{version_id}:label")
async def update_version_metadata(file_id: str, version_id: str, payload: VersionLabelRequest):
    version = runtime.api_gateway.update_version_metadata(
        file_id,
        version_id,
        label=payload.label,
        is_pinned=payload.is_pinned,
        autosave=payload.autosave,
        change_summary=payload.change_summary,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return _serialize_version(version)


@app.get("/search")
async def search(q: str, ctx: AuthContext = Depends(get_auth_context)):
    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    results = runtime.api_gateway.search(ctx.org_id, q)
    return [_serialize_search(doc) for doc in results]
