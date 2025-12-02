"""FastAPI-based gateway for external clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..runtime import CloudDriveRuntime
from ..messaging import MessageEnvelope
from ..storage.real_file_store import RealFileStore
from .grpc_server import build_grpc_server, configure_grpc_listener


runtime = CloudDriveRuntime.bootstrap()
real_file_store = RealFileStore(os.environ.get("REAL_FILE_STORE_DIR", os.path.join(os.getcwd(), "data", "real-files")))
_grpc_server = None
_grpc_bind = os.environ.get("CLOUD_DRIVE_GRPC_BIND", "0.0.0.0:50051")
_grpc_enabled = _grpc_bind.strip().lower() not in {"", "disable", "off", "0"}
if not _grpc_enabled:
    _grpc_bind = ""
_grpc_tls_cert = os.environ.get("CLOUD_DRIVE_GRPC_TLS_CERT")
_grpc_tls_key = os.environ.get("CLOUD_DRIVE_GRPC_TLS_KEY")
logger = logging.getLogger(__name__)
_demo_parent_folders: dict[str, str] = {}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _grpc_server
    if _grpc_server is None and _grpc_enabled:
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

_cors_origins = [origin.strip() for origin in os.environ.get("CLOUD_DRIVE_CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class NodeProvisionRequest(BaseModel):
    node_id: Optional[str] = None
    storage_gb: Optional[float] = Field(default=None, gt=0)
    bandwidth_mbps: Optional[int] = Field(default=None, gt=0)
    cpu_capacity: Optional[int] = Field(default=None, gt=0)
    memory_capacity: Optional[int] = Field(default=None, gt=0)
    zone: Optional[str] = None


class SimulationTickRequest(BaseModel):
    duration_seconds: float = Field(default=1.0, ge=0.01, le=120)
    run_background_jobs: bool = Field(default=True)


class DemoUploadRequest(BaseModel):
    file_name: Optional[str] = None
    size_mb: float = Field(default=5.0, gt=0.01, le=1024)
    parent_id: Optional[str] = None
    source_node: Optional[str] = None


class LinkUpdateRequest(BaseModel):
    node_a: str
    node_b: str
    bandwidth_mbps: Optional[int] = Field(default=None, gt=0)
    latency_ms: Optional[float] = Field(default=None, gt=0)


class LinkPairRequest(BaseModel):
    node_a: str
    node_b: str


class _CamelAliasModel(BaseModel):
    class Config:
        allow_population_by_field_name = True
        populate_by_name = True


class FilePushRequest(_CamelAliasModel):
    source_node: str = Field(alias="sourceNode")
    file_name: Optional[str] = Field(default=None, alias="fileName")
    size_mb: float = Field(default=10.0, gt=0.01, le=2048, alias="sizeMb")
    prefer_local: bool = Field(default=False, alias="preferLocal")


class FileTransferRequest(_CamelAliasModel):
    source_node: str = Field(alias="sourceNode")
    target_node: str = Field(alias="targetNode")
    file_name: str = Field(alias="fileName")
    size_mb: float = Field(default=10.0, gt=0.01, le=4096, alias="sizeMb")


class FileFetchRequest(_CamelAliasModel):
    target_node: str = Field(alias="targetNode")
    file_name: str = Field(alias="fileName")


class SimulationResetRequest(BaseModel):
    clear_saved: bool = Field(default=False)


class SimulationSnapshotRequest(BaseModel):
    path: Optional[str] = None


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


@app.get("/v1/storage/nodes")
async def get_cluster_nodes(include_replicas: bool = False):
    controller = getattr(runtime, "controller", None)
    if not controller:
        return []
    nodes = controller.list_node_status(include_replicas=include_replicas)
    return [_serialize_cluster_node(node) for node in nodes]


@app.get("/v1/transfers")
async def list_transfers(limit: int = 25):
    return _collect_transfer_rows(limit=max(0, limit))


@app.get("/v1/files")
async def list_recent_files(
    limit: int = 25,
    include_folders: bool = False,
    ctx: AuthContext = Depends(get_auth_context),
):
    capped_limit = max(0, min(limit, 100))
    entries = runtime.api_gateway.list_recent_files(
        limit=capped_limit,
        include_folders=include_folders,
        org_id=ctx.org_id,
    )
    return [_serialize_recent_file(entry) for entry in entries]


@app.get("/v1/files/catalog")
async def list_file_catalog(include_folders: bool = False, ctx: AuthContext = Depends(get_auth_context)):
    entries = runtime.api_gateway.list_all_files(include_folders=include_folders, org_id=ctx.org_id)
    return [_serialize_recent_file(entry) for entry in entries]


@app.get("/v1/activity")
async def list_recent_activity(limit: int = 10):
    events = list(runtime.api_gateway.list_activity())
    if limit > 0:
        events = events[-limit:]
    return [_serialize_activity_event(event) for event in reversed(events)]


@app.get("/v1/observability/slo/burn-rate")
async def get_burn_rate_series(limit: int = 12):
    return _build_burn_rate_series(limit=max(1, limit))


@app.get("/v1/auth/profile")
async def get_auth_profile(ctx: AuthContext = Depends(get_auth_context)):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=60)
    scopes = ctx.scopes or ctx.roles
    return {
        "userId": ctx.user_id,
        "orgId": ctx.org_id,
        "scopes": scopes,
        "expiresAt": expires_at.isoformat(),
    }


@app.get("/v1/observability/grafana/panels")
async def list_grafana_panels():
    return _build_grafana_panels()


@app.post("/v1/control/nodes")
async def provision_node(payload: NodeProvisionRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    existing_nodes = [row.node_id for row in controller.list_node_status(include_replicas=True)]
    node_id = payload.node_id or f"node-{uuid.uuid4().hex[:6]}"
    try:
        node = controller.add_node(
            node_id,
            storage_gb=payload.storage_gb,
            bandwidth_mbps=payload.bandwidth_mbps or 1200,
            cpu_capacity=payload.cpu_capacity or 8,
            memory_capacity=payload.memory_capacity or 32,
            zone=payload.zone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    for peer_id in existing_nodes:
        success = controller.connect_nodes(node.node_id, peer_id)
        if not success:
            logger.warning("Auto-connect failed between %s and %s", node.node_id, peer_id)
    runtime.run_background_jobs()
    status_rows = controller.list_node_status(include_replicas=True)
    serialized = next((row for row in status_rows if row.node_id == node.node_id), None)
    _record_ui_activity("Provision node", node.node_id, ctx, zone=node.zone)
    _ensure_active_transfers(ctx=ctx)
    return {
        "node": _serialize_cluster_node(serialized) if serialized else {"id": node.node_id},
        "message": f"Node {node.node_id} provisioned",
    }


@app.post("/v1/control/nodes/{node_id}:fail")
async def fail_node(node_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    success = controller.fail_node(node_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    _record_ui_activity("Fail node", node_id, ctx)
    _ensure_active_transfers(ctx=ctx)
    return {"node_id": node_id, "status": "failed"}


@app.post("/v1/control/nodes/{node_id}:restore")
async def restore_node(node_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    controller.restore_node(node_id)
    _record_ui_activity("Restore node", node_id, ctx)
    _ensure_active_transfers(ctx=ctx)
    return {"node_id": node_id, "status": "online"}


@app.delete("/v1/control/nodes/{node_id}")
async def remove_node(node_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    removed = controller.remove_node(node_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    runtime.run_background_jobs()
    _record_ui_activity("Remove node", node_id, ctx)
    return {"node_id": node_id, "removed": True}


@app.get("/v1/control/nodes/{node_id}")
async def inspect_node(node_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    info = controller.get_node_info(node_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return {"node": _serialize_node_detail(info)}


@app.get("/v1/control/clusters")
async def list_clusters(ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    return controller.get_clusters()


@app.get("/v1/control/events")
async def list_controller_events(limit: int = 25, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    limit = max(1, min(limit, 200))
    events = controller.recent_events(limit)
    return list(events)


@app.post("/v1/control/links")
async def connect_link(payload: LinkUpdateRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    success = controller.connect_nodes(payload.node_a, payload.node_b, payload.bandwidth_mbps, payload.latency_ms)
    if not success:
        raise HTTPException(status_code=404, detail="Unable to connect nodes")
    runtime.run_background_jobs()
    _record_ui_activity("Connect nodes", f"{payload.node_a}->{payload.node_b}", ctx)
    return {"connected": True}


@app.post("/v1/control/links:disconnect")
async def disconnect_link(payload: LinkPairRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    success = controller.disconnect_nodes(payload.node_a, payload.node_b)
    if not success:
        raise HTTPException(status_code=404, detail="Link not found")
    runtime.run_background_jobs()
    _record_ui_activity("Disconnect nodes", f"{payload.node_a}-{payload.node_b}", ctx)
    return {"disconnected": True}


@app.post("/v1/control/links:fail")
async def fail_link(payload: LinkPairRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    success = controller.fail_link(payload.node_a, payload.node_b)
    if not success:
        raise HTTPException(status_code=404, detail="Link not found")
    _record_ui_activity("Fail link", f"{payload.node_a}-{payload.node_b}", ctx)
    return {"failed": True}


@app.post("/v1/control/links:restore")
async def restore_link(payload: LinkPairRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    controller.restore_link(payload.node_a, payload.node_b)
    _record_ui_activity("Restore link", f"{payload.node_a}-{payload.node_b}", ctx)
    return {"restored": True}


@app.post("/v1/control/sim/tick")
async def run_simulation_step(payload: SimulationTickRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    controller.run_for(payload.duration_seconds)
    if payload.run_background_jobs:
        runtime.run_background_jobs()
    _record_ui_activity("Advance simulation", f"{payload.duration_seconds}s", ctx)
    _ensure_active_transfers(ctx=ctx)
    return {
        "duration": payload.duration_seconds,
        "metrics": runtime.get_metrics_snapshot(),
    }


@app.post("/v1/control/uploads/demo")
async def create_demo_upload(payload: DemoUploadRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    entry = _perform_demo_upload(ctx, payload)
    _record_ui_activity("Demo upload", entry.name, ctx, sizeBytes=entry.size_bytes)
    _ensure_active_transfers(minimum=4, ctx=ctx)
    return {"file": _serialize_recent_file(entry)}


@app.post("/v1/control/transfers/push")
async def push_file(payload: FilePushRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    file_name = payload.file_name or f"push-{uuid.uuid4().hex[:8]}.bin"
    size_bytes = int(payload.size_mb * 1024 * 1024)
    try:
        target_id, transfer = controller.push_file(
            payload.source_node,
            file_name,
            size_bytes,
            prefer_local=payload.prefer_local,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    controller.run_until_idle()
    runtime.run_background_jobs()
    summary = _serialize_transfer_detail(transfer, source=payload.source_node, target=target_id)
    _record_ui_activity("Push file", file_name, ctx, sizeBytes=size_bytes)
    _ensure_active_transfers(ctx=ctx)
    return {"transfer": summary, "targetNode": target_id}


@app.post("/v1/files/upload-real")
async def upload_real_file(
    source_node: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(get_auth_context),
):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    entry_node = _select_entry_node(source_node)
    if not entry_node:
        raise HTTPException(status_code=400, detail="No entry nodes available")
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")
    try:
        blob_path, size_bytes = real_file_store.save_stream(file.file, file.filename)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to persist upload: {exc}") from exc
    finally:
        await file.close()

    try:
        target_id, transfer = controller.push_file(entry_node, file.filename, size_bytes, prefer_local=False)
        controller.run_until_idle()
        runtime.run_background_jobs()
    except Exception as exc:  # pragma: no cover - defensive
        try:
            os.remove(blob_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Unable to ingest file: {exc}") from exc

    dataset_id = getattr(transfer, "backing_file_id", None) or getattr(transfer, "file_id") or uuid.uuid4().hex
    real_file_store.register_dataset(
        dataset_id,
        blob_path,
        file.filename,
        size_bytes,
        file_id=getattr(transfer, "file_id", None),
        file_name=getattr(transfer, "file_name", file.filename),
    )
    summary = _serialize_transfer_detail(transfer, source=entry_node, target=target_id)
    _record_ui_activity("Upload real file", file.filename, ctx, sizeBytes=size_bytes)
    return {
        "datasetId": dataset_id,
        "transfer": summary,
        "targetNode": target_id,
        "sizeBytes": size_bytes,
    }


@app.post("/v1/control/transfers/fetch")
async def fetch_file(payload: FileFetchRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    try:
        transfer = controller.pull_file(payload.target_node, payload.file_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    controller.run_until_idle()
    runtime.run_background_jobs()
    summary = _serialize_transfer_detail(transfer, target=payload.target_node)
    _record_ui_activity("Fetch file", payload.file_name, ctx)
    return {"transfer": summary}


@app.get("/v1/files/download-real/{dataset_id}")
async def download_real_file(dataset_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    entry = real_file_store.resolve(dataset_id)
    if not entry:
        entry = real_file_store.resolve_by_name(dataset_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if not _dataset_has_live_replicas(controller, dataset_id, entry):
        raise HTTPException(status_code=409, detail="Dataset unavailable; no healthy replicas online")
    file_path = entry.get("path")
    if not file_path:
        raise HTTPException(status_code=404, detail="Dataset missing path")
    original_name = entry.get("original_name") or f"dataset-{dataset_id[:8]}"
    try:
        file_handle = open(file_path, "rb")
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"Dataset unavailable: {exc}") from exc
    headers = {
        "Content-Disposition": f"attachment; filename=\"{original_name}\"",
    }
    _record_ui_activity("Download real file", dataset_id, ctx)
    return StreamingResponse(file_handle, media_type="application/octet-stream", headers=headers)


@app.post("/v1/control/transfers/initiate")
async def initiate_transfer(payload: FileTransferRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    size_bytes = int(payload.size_mb * 1024 * 1024)
    try:
        transfer = controller.initiate_transfer(payload.source_node, payload.target_node, payload.file_name, size_bytes)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    controller.run_until_idle()
    runtime.run_background_jobs()
    summary = _serialize_transfer_detail(transfer, source=payload.source_node, target=payload.target_node)
    _record_ui_activity("Manual transfer", payload.file_name, ctx, sizeBytes=size_bytes)
    return {"transfer": summary}


@app.post("/v1/control/sim/reset")
async def reset_simulation(payload: SimulationResetRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    controller.reset_state(clear_saved=payload.clear_saved)
    runtime.run_background_jobs()
    _record_ui_activity("Reset simulation", "controller", ctx, clearSaved=payload.clear_saved)
    return {"status": "reset"}


@app.post("/v1/control/sim/save")
async def save_simulation(payload: SimulationSnapshotRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    try:
        path = controller.save_snapshot(payload.path)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_ui_activity("Save snapshot", path or "default", ctx)
    return {"path": path}


@app.post("/v1/control/sim/restore")
async def restore_simulation(payload: SimulationSnapshotRequest, ctx: AuthContext = Depends(get_auth_context)):
    _ensure_ops_admin(ctx)
    controller = _get_controller()
    try:
        restored = controller.load_snapshot(payload.path)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not restored:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    runtime.run_background_jobs()
    _record_ui_activity("Restore snapshot", payload.path or "default", ctx)
    return {"restored": True}


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


def _serialize_cluster_node(node_status) -> dict:
    capacity_gb = _bytes_to_gb(node_status.storage_total)
    used_gb = _bytes_to_gb(node_status.storage_used)
    return {
        "id": node_status.node_id,
        "zone": node_status.zone or "unknown",
        "status": _classify_node_health(node_status, used_gb, capacity_gb),
        "storageUsedGb": used_gb,
        "storageCapacityGb": capacity_gb,
        "replicaParent": getattr(node_status, "replicas", None),
        "isReplica": bool(getattr(node_status, "replicas", None)),
    }


def _classify_node_health(node_status, used_gb: float, capacity_gb: float) -> str:
    if not getattr(node_status, "online", False):
        return "offline"
    utilization = 0.0
    if capacity_gb > 0:
        utilization = used_gb / capacity_gb
    if utilization >= 0.9:
        return "degraded"
    return "healthy"


def _bytes_to_gb(value: int) -> float:
    return round(value / (1024 ** 3), 2) if value else 0.0


def _collect_transfer_rows(limit: int) -> list[dict]:
    controller = getattr(runtime, "controller", None)
    network = getattr(controller, "network", None)
    if not controller or not network:
        return []
    rows: list[dict] = []
    operations = getattr(network, "transfer_operations", {}) or {}
    now = time.time()
    for source_id, transfers in operations.items():
        for transfer_id, transfer in transfers.items():
            rows.append(_serialize_transfer(source_id, transfer_id, transfer, now))
    rows.sort(key=lambda row: row.pop("_sort_key", 0), reverse=True)
    if limit:
        rows = rows[:limit]
    return rows


def _serialize_transfer(source_id: str, transfer_id: str, transfer, now: float) -> dict:
    total_bytes = max(1, getattr(transfer, "total_size", 0) or 0)
    completed_bytes = 0
    for chunk in getattr(transfer, "chunks", []) or []:
        status_name = getattr(getattr(chunk, "status", None), "name", str(getattr(chunk, "status", "")))
        if status_name == "COMPLETED":
            completed_bytes += getattr(chunk, "size", 0)
    completed_bytes = min(completed_bytes, total_bytes)
    progress_pct = round((completed_bytes / total_bytes) * 100, 1)
    eta_seconds = _estimate_eta_seconds(transfer, completed_bytes, now)
    identifier = transfer_id or getattr(transfer, "file_id", None) or f"transfer:{source_id}:{int(now)}"
    direction = "download" if getattr(transfer, "is_retrieval", False) else "upload"
    return {
        "id": str(identifier),
        "filename": getattr(transfer, "file_name", None) or getattr(transfer, "file_id", "unknown-object"),
        "progress": progress_pct,
        "direction": direction,
        "etaSeconds": eta_seconds,
        "_sort_key": getattr(transfer, "created_at", 0.0) or 0.0,
    }


def _estimate_eta_seconds(transfer, completed_bytes: int, now: float) -> int:
    status_name = getattr(getattr(transfer, "status", None), "name", str(getattr(transfer, "status", "")))
    if status_name == "COMPLETED":
        return 0
    started = getattr(transfer, "created_at", None)
    if not started:
        return 0
    elapsed = max(now - started, 1.0)
    throughput = completed_bytes / elapsed if elapsed > 0 else 0
    remaining = max(0, getattr(transfer, "total_size", 0) - completed_bytes)
    if throughput <= 0:
        return 0
    return int(round(remaining / throughput))


def _serialize_recent_file(entry) -> dict:
    return {
        "id": entry.id,
        "name": entry.name,
        "owner": entry.created_by,
        "sizeBytes": entry.size_bytes,
        "updatedAt": entry.updated_at.isoformat(),
        "createdAt": entry.created_at.isoformat() if entry.created_at else entry.updated_at.isoformat(),
        "mimeType": entry.mime_type,
        "isFolder": bool(entry.is_folder),
    }


def _serialize_activity_event(envelope) -> dict:
    payload = getattr(envelope, "payload", {}) or {}
    timestamp = payload.get("timestamp")
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()
    actor = payload.get("actor") or payload.get("created_by") or payload.get("user_id") or "system"
    target = (
        payload.get("target")
        or payload.get("file_id")
        or payload.get("session_id")
        or payload.get("resource_id")
        or "n/a"
    )
    action = payload.get("action") or envelope.topic.replace("_", " ")
    event_id = payload.get("event_id") or f"{envelope.topic}:{payload.get('session_id') or uuid.uuid4().hex}"
    return {
        "id": str(event_id),
        "actor": actor,
        "action": action,
        "target": str(target),
        "timestamp": timestamp,
    }


def _serialize_node_detail(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    detail = {
        "nodeId": info.get("node_id"),
        "online": info.get("online", False),
        "zone": info.get("zone"),
        "bandwidth": info.get("bandwidth"),
        "replicaParent": info.get("replica_parent"),
        "replicaChildren": info.get("replica_children", []),
        "neighbors": info.get("neighbors", []),
        "storedFiles": info.get("stored_files", []),
        "activeTransfers": info.get("active_transfers", []),
    }
    telemetry = info.get("telemetry") or {}
    if telemetry:
        detail["telemetry"] = telemetry
    detail["usageBytes"] = {
        "used": info.get("used_storage", 0),
        "total": info.get("total_storage", 0),
        "available": info.get("available_storage", 0),
    }
    return detail


def _serialize_transfer_detail(transfer, *, source: Optional[str] = None, target: Optional[str] = None) -> dict:
    source_id = source or getattr(transfer, "source_node", None) or getattr(transfer, "source", None)
    target_id = target or getattr(transfer, "target_node", None)
    status = getattr(getattr(transfer, "status", None), "name", str(getattr(transfer, "status", "pending")))
    return {
        "fileId": getattr(transfer, "file_id", None),
        "fileName": getattr(transfer, "file_name", None),
        "sizeBytes": getattr(transfer, "total_size", 0),
        "status": status,
        "chunks": len(getattr(transfer, "chunks", []) or []),
        "source": source_id,
        "target": target_id,
        "createdAt": getattr(transfer, "created_at", None),
        "completedAt": getattr(transfer, "completed_at", None),
    }


def _record_ui_activity(action: str, target: str, ctx: Optional[AuthContext] = None, **extra: Any) -> None:
    payload = {
        "action": action,
        "target": target,
        "actor": getattr(ctx, "user_id", "system"),
        "org_id": getattr(ctx, "org_id", "demo"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    bus = getattr(runtime, "bus", None)
    if bus is None:
        return
    bus.publish(MessageEnvelope(topic="ui.activity", payload=payload))


def _ensure_active_transfers(minimum: int = 3, *, ctx: Optional[AuthContext] = None) -> int:
    controller = getattr(runtime, "controller", None)
    network = getattr(controller, "network", None)
    if not controller or not network:
        return 0
    active = sum(len(group) for group in getattr(network, "transfer_operations", {}).values())
    deficit = max(0, minimum - active)
    if deficit <= 0:
        return 0
    return _spawn_transfer_burst(controller, deficit, ctx=ctx)


def _select_entry_node(preferred_id: Optional[str] = None) -> Optional[str]:
    controller = getattr(runtime, "controller", None)
    if not controller:
        return preferred_id
    nodes = controller.list_node_status(include_replicas=False)
    if preferred_id and any(row.node_id == preferred_id for row in nodes):
        return preferred_id
    return nodes[0].node_id if nodes else preferred_id


def _dataset_has_live_replicas(controller, dataset_id: str, entry: dict[str, Any]) -> bool:
    network = getattr(controller, "network", None)
    if network is None or not hasattr(network, "locate_file"):
        return True
    seen: set[str] = set()
    candidates: list[str] = []
    raw_tokens: list[Any] = [dataset_id, entry.get("file_id"), entry.get("file_name"), entry.get("original_name")]
    for token in raw_tokens:
        if token is None:
            continue
        identifier = str(token)
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        candidates.append(identifier)
    for identifier in candidates:
        matches = network.locate_file(identifier)
        if matches:
            return True
    return False


def _spawn_transfer_burst(controller, count: int, *, ctx: Optional[AuthContext] = None) -> int:
    network = getattr(controller, "network", None)
    nodes = [node_id for node_id in getattr(network, "nodes", {}).keys()]
    if len(nodes) < 2:
        return 0
    rng = random.Random()
    started = 0
    attempts = 0
    max_attempts = max(4, count * 4)
    while started < count and attempts < max_attempts:
        attempts += 1
        try:
            source, target = rng.sample(nodes, 2)
        except ValueError:
            break
        size_mb = rng.randint(40, 180)
        file_name = f"pipeline-{uuid.uuid4().hex[:8]}.bin"
        try:
            controller.initiate_transfer(source, target, file_name, size_mb * 1024 * 1024)
        except RuntimeError:
            continue
        started += 1
        _record_ui_activity(
            "Pipeline transfer",
            f"{source} -> {target}",
            ctx,
            file=file_name,
            sizeMb=size_mb,
        )
    if started:
        controller.run_for(0.2)
    return started


def _build_burn_rate_series(limit: int) -> list[dict]:
    manager = getattr(runtime, "observability_manager", None)
    telemetry = getattr(runtime, "telemetry", None)
    if limit <= 0 or not manager or not telemetry:
        return []
    slo = _primary_latency_slo(manager)
    metric_name = slo.metric if slo else "ingest.p95_ms"
    threshold = slo.threshold if slo else max(1.0, runtime.get_metrics_snapshot().get(metric_name, 1.0))
    metrics = [
        metric for metric in telemetry.metrics if metric.get("name") == metric_name and metric.get("timestamp")
    ]
    metrics.sort(key=lambda metric: metric.get("timestamp"))
    points = []
    for metric in metrics[-limit:]:
        burn = _normalize_burn(metric.get("value", 0.0), threshold)
        points.append({"timestamp": metric["timestamp"], "burnRate": burn})
    if points:
        return points
    snapshot = runtime.get_metrics_snapshot()
    base_value = snapshot.get(metric_name, threshold)
    now = datetime.now(timezone.utc)
    interval = 5
    return [
        {
            "timestamp": (now - timedelta(minutes=interval * idx)).isoformat(),
            "burnRate": _normalize_burn(base_value * (1 + (idx * 0.02)), threshold),
        }
        for idx in reversed(range(limit))
    ]


def _primary_latency_slo(manager) -> Optional[Any]:
    for slo in getattr(manager, "slo_definitions", []):
        if getattr(slo, "metric", "").startswith("ingest"):
            return slo
    return next(iter(getattr(manager, "slo_definitions", [])), None)


def _normalize_burn(value: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.0
    return round(min(2.5, max(0.0, value / threshold)), 3)


def _build_grafana_panels() -> list[dict]:
    telemetry = getattr(runtime, "telemetry", None)
    snapshot = runtime.get_metrics_snapshot()
    panel_specs = [
        {
            "id": "ingest-latency",
            "title": "Upload latency p95",
            "metric": "ingest.p95_ms",
            "unit": "ms",
            "description": "Tail latency for ingest requests",
        },
        {
            "id": "storage-utilization",
            "title": "Storage utilization",
            "metric": "storage.utilization",
            "unit": "%",
            "percent": True,
            "description": "Total consumed storage across CloudSim",
        },
        {
            "id": "replication-queue",
            "title": "Replica queue depth",
            "metric": "replication.queue_depth",
            "unit": "ops",
            "description": "Outstanding replica operations",
        },
    ]
    panels: list[dict] = []
    for spec in panel_specs:
        history = _metric_history(spec["metric"], telemetry, limit=30)
        latest = history[-1] if history else snapshot.get(spec["metric"], 0.0)
        html = _render_panel_html(
            spec["title"],
            latest,
            history,
            unit=spec.get("unit", ""),
            percent=spec.get("percent", False),
        )
        panels.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "iframeUrl": _encode_data_iframe(html),
                "description": spec.get("description"),
            }
        )
    return panels


def _metric_history(metric: str, telemetry, limit: int = 20) -> list[float]:
    if not telemetry:
        return []
    values = [
        float(entry.get("value", 0.0))
        for entry in telemetry.metrics
        if entry.get("name") == metric and entry.get("value") is not None
    ]
    if not values:
        return []
    return values[-limit:]


def _render_panel_html(title: str, value: float, history: list[float], *, unit: str = "", percent: bool = False) -> str:
    series = history[:] if history else [value]
    display_series = series
    display_value = value
    if percent:
        display_series = [val * 100.0 for val in series]
        display_value = value * 100.0
    value_text = f"{display_value:.1f} {unit}".strip()
    svg = _sparkline_svg(display_series)
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <style>
      body {{ margin: 0; font-family: 'Segoe UI', sans-serif; background: #020617; color: #e2e8f0; }}
      .wrap {{ padding: 14px 16px; }}
      .title {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.3em; color: #94a3b8; }}
      .value {{ font-size: 28px; font-weight: 600; margin-top: 4px; }}
      svg {{ width: 100%; height: 120px; margin-top: 12px; }}
      polyline {{ fill: none; stroke: #38bdf8; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }}
    </style>
  </head>
  <body>
    <div class=\"wrap\">
      <div class=\"title\">{title}</div>
      <div class=\"value\">{value_text}</div>
      {svg}
    </div>
  </body>
</html>
""".strip()


def _sparkline_svg(values: list[float]) -> str:
    if not values:
        values = [0.0]
    width, height = 360.0, 120.0
    min_val = min(values)
    max_val = max(values)
    span = max(max_val - min_val, 1e-6)
    count = max(1, len(values) - 1)
    coords = []
    for idx, val in enumerate(values):
        x = (idx / count) * (width - 20.0) + 10.0
        normalized = (val - min_val) / span
        y = height - (normalized * (height - 30.0)) - 10.0
        coords.append(f"{x:.1f},{y:.1f}")
    points = " ".join(coords)
    gradient = """
    <defs>
      <linearGradient id=\"grad\" x1=\"0%\" y1=\"0%\" x2=\"0%\" y2=\"100%\">
        <stop offset=\"0%\" style=\"stop-color:#38bdf8;stop-opacity:0.4\" />
        <stop offset=\"100%\" style=\"stop-color:#38bdf8;stop-opacity:0\" />
      </linearGradient>
    </defs>
    """
    polyline = f"<polyline points=\"{points}\" />"
    return f"<svg viewBox=\"0 0 {width} {height}\">{gradient}<g>{polyline}</g></svg>"


def _encode_data_iframe(html: str) -> str:
    data = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{data}"


def _perform_demo_upload(ctx: AuthContext, payload: DemoUploadRequest):
    controller = _get_controller()
    nodes = list(getattr(controller.network, "nodes", {}))
    if not nodes:
        raise HTTPException(status_code=503, detail="No storage nodes available")
    source_node = payload.source_node or nodes[0]
    file_name = payload.file_name or f"demo-object-{uuid.uuid4().hex[:6]}.bin"
    size_bytes = max(1, int(payload.size_mb * 1024 * 1024))
    parent_id = payload.parent_id or _ensure_demo_folder(ctx.org_id, ctx.user_id)

    session = runtime.api_gateway.start_upload(
        ctx.org_id,
        parent_id,
        size_bytes,
        ctx.user_id,
        client_hints={"ui_action": "demo_upload"},
    )
    chunk_size = max(1, session.chunk_size or runtime.config.storage.default_chunk_size)
    _append_chunks(session.session_id, source_node, file_name, size_bytes, chunk_size)
    runtime.api_gateway.finalize_upload(session.session_id)
    runtime.run_background_jobs()
    entry = runtime.metadata_service.get_file(session.file_id)
    if entry is None:
        raise HTTPException(status_code=500, detail="Upload finalized but file metadata missing")
    return entry


def _append_chunks(session_id: str, source_node: str, file_name: str, total_size: int, chunk_size: int) -> None:
    remaining = total_size
    chunk_id = 0
    offset = 0
    while remaining > 0:
        length = min(chunk_size, remaining)
        runtime.api_gateway.append_chunk(
            session_id,
            source_node,
            file_name,
            length,
            chunk_id=chunk_id,
            offset=offset,
        )
        remaining -= length
        offset += length
        chunk_id += 1


def _ensure_demo_folder(org_id: str, user_id: str) -> str:
    folder_id = _demo_parent_folders.get(org_id)
    if folder_id:
        entry = runtime.metadata_service.get_file(folder_id, include_deleted=True)
        if entry and not entry.deleted_at:
            return folder_id
    folder_name = f"demo-uploads-{org_id}"
    folder = runtime.metadata_service.create_folder(org_id, None, folder_name, user_id)
    _demo_parent_folders[org_id] = folder.id
    return folder.id


def _get_controller():
    controller = getattr(runtime, "controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Controller unavailable")
    return controller


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
