"""FastAPI-based gateway for external clients."""

from __future__ import annotations

import logging
import os
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
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


async def get_auth_context() -> AuthContext:
    """Placeholder auth dependency; would parse JWT headers in real deployment."""
    return AuthContext(user_id="user-123", org_id="org-123")


class FolderCreateRequest(BaseModel):
    name: str
    parent_id: Optional[str] = Field(default=None)


class UploadInitRequest(BaseModel):
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


@app.post("/folders")
async def create_folder(payload: FolderCreateRequest, ctx: AuthContext = Depends(get_auth_context)):
    folder = runtime.api_gateway.create_folder(ctx.org_id, payload.parent_id, payload.name, ctx.user_id)
    return folder.__dict__


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
        payload.parent_id,
        payload.size_bytes,
        ctx.user_id,
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
async def share_file(file_id: str, payload: ShareRequest):
    runtime.api_gateway.grant_share(file_id, payload.principal)
    return {"status": "granted"}


@app.get("/activity")
async def list_activity():
    return runtime.api_gateway.list_activity()
