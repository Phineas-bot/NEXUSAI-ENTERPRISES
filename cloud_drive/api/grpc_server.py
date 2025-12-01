"""gRPC control-plane server exposing Files/Uploads/Sharing services."""

from __future__ import annotations

import argparse
import json
import logging
import math
import threading
from concurrent import futures
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from ..models import FileEntry, FileVersion, UploadSession
from ..services.observability_service import SLODefinition
from ..runtime import CloudDriveRuntime
from . import control_plane_pb2 as pb2
from . import control_plane_pb2_grpc as pb2_grpc


def _ts(dt: Optional[datetime]) -> Timestamp:
    stamp = Timestamp()
    if dt is not None:
        stamp.FromDatetime(dt)
    return stamp


def _file_entry_to_proto(entry: FileEntry, versions: Optional[Iterable[FileVersion]] = None) -> pb2.FileResource:
    resource = pb2.FileResource(
        id=entry.id,
        org_id=entry.org_id,
        parent_id=entry.parent_id or "",
        name=entry.name,
        mime_type=entry.mime_type,
        size_bytes=entry.size_bytes,
        checksum=entry.checksum or "",
        is_folder=entry.is_folder,
        created_by=entry.created_by,
    )
    resource.created_at.CopyFrom(_ts(entry.created_at))
    resource.updated_at.CopyFrom(_ts(entry.updated_at))
    if versions:
        for version in versions:
            resource.versions.append(_version_to_proto(version))
    return resource


def _version_to_proto(version: FileVersion) -> pb2.FileVersion:
    message = pb2.FileVersion(
        id=version.version_id,
        file_id=version.file_id,
        manifest_id=version.manifest_id,
        size_bytes=version.size_bytes,
        version_number=version.version_number,
        retention_tier="hot",
        created_by=version.created_by,
    )
    message.created_at.CopyFrom(_ts(version.created_at))
    return message


def _upload_session_to_proto(session: UploadSession) -> pb2.UploadSession:
    message = pb2.UploadSession(
        session_id=session.session_id,
        file_id=session.file_id or "",
        parent_id=session.parent_id,
        expected_size=session.expected_size,
        chunk_size=session.chunk_size,
        created_by=session.created_by,
        received_bytes=session.received_bytes,
        checksum="",
    )
    message.expires_at.CopyFrom(_ts(session.expires_at))
    return message


def _share_to_proto(file_id: str, principal: str) -> pb2.Share:
    return pb2.Share(
        id=f"{file_id}:{principal}",
        file_id=file_id,
        principal_type="user",
        principal_id=principal,
        permission="editor",
    )


def _slo_to_proto(slo: SLODefinition) -> pb2.SLO:
    return pb2.SLO(
        name=slo.name,
        metric=slo.metric,
        threshold=float(slo.threshold),
        comparator=slo.comparator,
        window_minutes=slo.window_minutes,
    )


def _ops_admin_roles(runtime: CloudDriveRuntime) -> set[str]:
    config_roles = getattr(runtime.config.auth, "ops_admin_roles", None)
    return set(config_roles or ["ops.admin"])


class OperationStore:
    """Thread-safe in-memory registry for LRO metadata."""

    def __init__(self) -> None:
        self._ops: Dict[str, pb2.Operation] = {}
        self._lock = threading.Lock()

    def save(self, operation: pb2.Operation) -> None:
        with self._lock:
            self._ops[operation.operation_id] = operation

    def get(self, operation_id: str) -> Optional[pb2.Operation]:
        with self._lock:
            stored = self._ops.get(operation_id)
            if stored is None:
                return None
            # Return a copy to avoid accidental mutation.
            clone = pb2.Operation()
            clone.CopyFrom(stored)
            return clone

    def cancel(self, operation_id: str) -> Optional[pb2.Operation]:
        with self._lock:
            stored = self._ops.get(operation_id)
            if stored is None:
                return None
            stored.done = True
            return stored


def _resolve_org(req_ctx: pb2.RequestContext) -> str:
    return req_ctx.org_id or "org-default"


def _resolve_user(req_ctx: pb2.RequestContext) -> str:
    return req_ctx.user_id or "system"


class FilesServiceHandler(pb2_grpc.FilesServiceServicer):
    def __init__(self, runtime: CloudDriveRuntime) -> None:
        self.runtime = runtime

    def CreateFile(self, request: pb2.CreateFileRequest, context: grpc.ServicerContext) -> pb2.FileResource:  # noqa: N802
        if not request.is_folder:
            context.abort(grpc.StatusCode.UNIMPLEMENTED, "Binary uploads must use UploadsService")
        entry = self.runtime.api_gateway.create_folder(
            _resolve_org(request.context),
            request.parent_id or None,
            request.name,
            _resolve_user(request.context),
        )
        return _file_entry_to_proto(entry)

    def GetFile(self, request: pb2.GetFileRequest, context: grpc.ServicerContext) -> pb2.FileResource:  # noqa: N802
        try:
            entry = self.runtime.api_gateway.get_file(request.file_id)
        except KeyError:
            context.abort(grpc.StatusCode.NOT_FOUND, f"file {request.file_id} not found")
        versions = self.runtime.api_gateway.list_versions(request.file_id)
        return _file_entry_to_proto(entry, versions)

    def ListFiles(self, request: pb2.ListFilesRequest, context: grpc.ServicerContext) -> pb2.ListFilesResponse:  # noqa: N802
        children = self.runtime.api_gateway.list_children(request.parent_id or None)
        return pb2.ListFilesResponse(files=[_file_entry_to_proto(ch) for ch in children])


class UploadsServiceHandler(pb2_grpc.UploadsServiceServicer):
    def __init__(self, runtime: CloudDriveRuntime, operation_store: OperationStore) -> None:
        self.runtime = runtime
        self.operation_store = operation_store

    def CreateSession(self, request: pb2.CreateUploadSessionRequest, context: grpc.ServicerContext) -> pb2.CreateUploadSessionResponse:  # noqa: N802
        if not request.parent_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "parent_id is required")
        org_id = _resolve_org(request.context)
        session = self.runtime.api_gateway.start_upload(
            org_id,
            request.parent_id,
            request.size_bytes,
            _resolve_user(request.context),
            file_id=request.file_id or None,
            chunk_size=request.chunk_size or None,
        )
        proto_session = _upload_session_to_proto(session)
        return pb2.CreateUploadSessionResponse(session=proto_session)

    def AppendChunk(self, request: pb2.AppendUploadChunkRequest, context: grpc.ServicerContext) -> pb2.AppendUploadChunkResponse:  # noqa: N802
        if not request.session_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "session_id is required")
        if not request.source_node:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "source_node is required")
        file_name = request.file_name or f"chunk-{request.chunk_id}"
        chunk_bytes = request.chunk_bytes or len(request.data)
        if chunk_bytes <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "chunk_bytes must be positive")
        chunk_id = request.chunk_id if request.chunk_id or request.chunk_id == 0 else None
        offset = request.offset if request.offset or request.offset == 0 else None
        self.runtime.api_gateway.append_chunk(
            request.session_id,
            request.source_node,
            file_name,
            chunk_bytes,
            chunk_id=chunk_id,
            offset=offset,
        )
        session = self.runtime.api_gateway.get_upload_session(request.session_id)
        return pb2.AppendUploadChunkResponse(received_bytes=session.received_bytes)

    def Finalize(self, request: pb2.FinalizeUploadRequest, context: grpc.ServicerContext) -> pb2.FinalizeUploadResponse:  # noqa: N802
        try:
            session = self.runtime.api_gateway.get_upload_session(request.session_id)
        except KeyError:
            context.abort(grpc.StatusCode.NOT_FOUND, f"session {request.session_id} not found")
        try:
            manifest = self.runtime.api_gateway.finalize_upload(request.session_id)
        except RuntimeError as exc:  # pragma: no cover - defensive
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        resource_id = session.file_id or ""
        manifest_id = (manifest.manifest_id if manifest else session.manifest_id) or ""
        total_chunks = math.ceil(session.expected_size / session.chunk_size) if session.chunk_size else 0
        completed_chunks = session.received_bytes // session.chunk_size if session.chunk_size else 0
        metadata = pb2.OperationMetadata(
            resource_id=resource_id,
            manifest_id=manifest_id,
            total_chunks=total_chunks,
            completed_chunks=completed_chunks,
            started_at=_ts(datetime.now(timezone.utc)),
            updated_at=_ts(datetime.now(timezone.utc)),
        )
        operation = pb2.Operation(
            operation_id=f"uploads/{request.session_id}:finalize",
            done=True,
            metadata=metadata,
        )
        self.operation_store.save(operation)
        return pb2.FinalizeUploadResponse(operation=operation)

    def Abort(self, request: pb2.AbortUploadRequest, context: grpc.ServicerContext) -> pb2.AbortUploadResponse:  # noqa: N802
        try:
            self.runtime.api_gateway.abort_upload(request.session_id)
        except KeyError:
            context.abort(grpc.StatusCode.NOT_FOUND, f"session {request.session_id} not found")
        return pb2.AbortUploadResponse()

    def DownloadChunks(self, request: pb2.DownloadChunkRequest, context: grpc.ServicerContext):  # noqa: N802
        if not request.file_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "file_id is required")
        length = request.length if request.length > 0 else None
        try:
            chunk_iter = self.runtime.api_gateway.stream_download(
                request.file_id,
                offset=request.offset,
                length=length,
            )
        except KeyError:
            context.abort(grpc.StatusCode.NOT_FOUND, f"file {request.file_id} not found")
        except ValueError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except RuntimeError as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))

        sent = False
        last_flag = False
        for offset, blob, is_last in chunk_iter:
            message = pb2.DownloadChunk(data=blob, offset=offset, eof=is_last)
            sent = True
            last_flag = is_last
            yield message

        if not sent or not last_flag:
            eof_offset = request.offset if not sent else offset + len(blob)
            yield pb2.DownloadChunk(data=b"", offset=eof_offset, eof=True)


class OperationsServiceHandler(pb2_grpc.OperationsServiceServicer):
    def __init__(self, operation_store: OperationStore) -> None:
        self.operation_store = operation_store

    def Get(self, request: pb2.GetOperationRequest, context: grpc.ServicerContext) -> pb2.Operation:  # noqa: N802
        operation = self.operation_store.get(request.operation_id)
        if operation is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"operation {request.operation_id} not found")
        return operation

    def Cancel(self, request: pb2.CancelOperationRequest, context: grpc.ServicerContext) -> pb2.CancelOperationResponse:  # noqa: N802
        operation = self.operation_store.cancel(request.operation_id)
        if operation is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"operation {request.operation_id} not found")
        return pb2.CancelOperationResponse()


class SharingServiceHandler(pb2_grpc.SharingServiceServicer):
    def __init__(self, runtime: CloudDriveRuntime) -> None:
        self.runtime = runtime

    def ShareFile(self, request: pb2.ShareFileRequest, context: grpc.ServicerContext) -> pb2.ShareFileResponse:  # noqa: N802
        if not request.file_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "file_id is required")
        if not request.grants:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "grants are required")
        for grant in request.grants:
            principal = grant.principal_id or grant.principal_type or grant.id
            if not principal:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "grants must include principal_id")
            self.runtime.api_gateway.grant_share(request.file_id, principal)
        principals = self.runtime.api_gateway.list_shares(request.file_id)
        return pb2.ShareFileResponse(grants=[_share_to_proto(request.file_id, p) for p in principals])

    def ListShares(self, request: pb2.ListSharesRequest, context: grpc.ServicerContext) -> pb2.ListSharesResponse:  # noqa: N802
        principals = self.runtime.api_gateway.list_shares(request.file_id)
        return pb2.ListSharesResponse(grants=[_share_to_proto(request.file_id, p) for p in principals])


class ObservabilityServiceHandler(pb2_grpc.ObservabilityServiceServicer):
    def __init__(self, runtime: CloudDriveRuntime) -> None:
        self.runtime = runtime

    def ListDashboards(self, request: pb2.ListDashboardsRequest, context: grpc.ServicerContext) -> pb2.ListDashboardsResponse:  # noqa: N802
        manager = getattr(self.runtime, "observability_manager", None)
        dashboards = []
        if manager:
            for dashboard_id, definition in manager.dashboards.items():
                title = str(definition.get("title", dashboard_id)) if isinstance(definition, dict) else dashboard_id
                try:
                    definition_json = json.dumps(definition)
                except TypeError:
                    definition_json = json.dumps({"raw": str(definition)})
                dashboards.append(
                    pb2.Dashboard(
                        id=dashboard_id,
                        title=title,
                        definition_json=definition_json,
                    )
                )
        return pb2.ListDashboardsResponse(dashboards=dashboards)

    def ListSLOs(self, request: pb2.ListSLOsRequest, context: grpc.ServicerContext) -> pb2.ListSLOsResponse:  # noqa: N802
        manager = getattr(self.runtime, "observability_manager", None)
        if not manager:
            context.abort(grpc.StatusCode.UNAVAILABLE, "observability manager unavailable")
        return pb2.ListSLOsResponse(slos=[_slo_to_proto(slo) for slo in manager.slo_definitions])

    def UpsertSLO(self, request: pb2.UpsertSLORequest, context: grpc.ServicerContext) -> pb2.UpsertSLOResponse:  # noqa: N802
        manager = getattr(self.runtime, "observability_manager", None)
        if not manager:
            context.abort(grpc.StatusCode.UNAVAILABLE, "observability manager unavailable")
        self._require_admin(request.context, context)
        slo_proto = request.slo
        if not slo_proto.name:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "slo.name is required")
        if not slo_proto.metric:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "slo.metric is required")
        slo = SLODefinition(
            name=slo_proto.name,
            metric=slo_proto.metric,
            threshold=slo_proto.threshold,
            comparator=slo_proto.comparator or ">=",
            window_minutes=slo_proto.window_minutes or 1,
        )
        stored = manager.upsert_slo(slo)
        return pb2.UpsertSLOResponse(slo=_slo_to_proto(stored))

    def DeleteSLO(self, request: pb2.DeleteSLORequest, context: grpc.ServicerContext) -> pb2.DeleteSLOResponse:  # noqa: N802
        manager = getattr(self.runtime, "observability_manager", None)
        if not manager:
            context.abort(grpc.StatusCode.UNAVAILABLE, "observability manager unavailable")
        self._require_admin(request.context, context)
        deleted = manager.remove_slo(request.name)
        return pb2.DeleteSLOResponse(deleted=deleted)

    def _require_admin(self, ctx: Optional[pb2.RequestContext], grpc_context: grpc.ServicerContext) -> None:
        scopes = set(ctx.scopes) if ctx else set()
        if scopes.intersection(_ops_admin_roles(self.runtime)):
            return
        grpc_context.abort(grpc.StatusCode.PERMISSION_DENIED, "ops.admin role required")


def build_grpc_server(runtime: Optional[CloudDriveRuntime] = None, *, max_workers: int = 8) -> grpc.Server:
    """Create a configured gRPC server without binding ports."""

    runtime = runtime or CloudDriveRuntime.bootstrap()
    operation_store = OperationStore()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb2_grpc.add_FilesServiceServicer_to_server(FilesServiceHandler(runtime), server)
    pb2_grpc.add_UploadsServiceServicer_to_server(UploadsServiceHandler(runtime, operation_store), server)
    pb2_grpc.add_OperationsServiceServicer_to_server(OperationsServiceHandler(operation_store), server)
    pb2_grpc.add_SharingServiceServicer_to_server(SharingServiceHandler(runtime), server)
    pb2_grpc.add_ObservabilityServiceServicer_to_server(ObservabilityServiceHandler(runtime), server)
    return server


def configure_grpc_listener(
    server: grpc.Server,
    bind: str,
    *,
    tls_cert_path: Optional[str] = None,
    tls_key_path: Optional[str] = None,
) -> int:
    """Attach either an insecure or TLS-secured listener to the server."""

    if tls_cert_path and not tls_key_path:
        raise ValueError("CLOUD_DRIVE_GRPC_TLS_KEY is required when TLS cert is provided")
    if tls_key_path and not tls_cert_path:
        raise ValueError("CLOUD_DRIVE_GRPC_TLS_CERT is required when TLS key is provided")

    if tls_cert_path and tls_key_path:
        cert_bytes = Path(tls_cert_path).expanduser().read_bytes()
        key_bytes = Path(tls_key_path).expanduser().read_bytes()
        credentials = grpc.ssl_server_credentials([(key_bytes, cert_bytes)])
        port = server.add_secure_port(bind, credentials)
    else:
        port = server.add_insecure_port(bind)

    if port == 0:
        raise RuntimeError(f"Failed to bind gRPC server to {bind}")
    return port


def _serve(bind: str, max_workers: int, *, tls_cert: Optional[str] = None, tls_key: Optional[str] = None) -> None:
    server = build_grpc_server(max_workers=max_workers)
    configure_grpc_listener(server, bind, tls_cert_path=tls_cert, tls_key_path=tls_key)
    server.start()
    logging.info("gRPC server listening on %s", bind)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logging.info("Shutting down gRPC server")
        server.stop(0)


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Cloud Drive gRPC control-plane server")
    parser.add_argument("--bind", default="0.0.0.0:50051", help="Address:port for the gRPC server")
    parser.add_argument("--max-workers", type=int, default=8, help="Thread pool size for gRPC handlers")
    parser.add_argument("--tls-cert", help="Path to PEM-encoded server certificate")
    parser.add_argument("--tls-key", help="Path to PEM-encoded private key")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    _serve(args.bind, args.max_workers, tls_cert=args.tls_cert, tls_key=args.tls_key)


if __name__ == "__main__":
    main()
