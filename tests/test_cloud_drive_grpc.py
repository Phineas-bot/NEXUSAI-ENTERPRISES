"""Basic smoke tests for the Cloud Drive gRPC endpoints."""

from __future__ import annotations

import grpc
import pytest

from cloud_drive.runtime import CloudDriveRuntime
from cloud_drive.api import control_plane_pb2 as pb2
from cloud_drive.api import control_plane_pb2_grpc as pb2_grpc
from cloud_drive.api.grpc_server import build_grpc_server


@pytest.fixture()
def grpc_channel():
    runtime = CloudDriveRuntime.bootstrap()
    runtime.controller.add_node("node1")
    server = build_grpc_server(runtime)
    port = server.add_insecure_port("localhost:0")
    server.start()

    channel = grpc.insecure_channel(f"localhost:{port}")
    grpc.channel_ready_future(channel).result(timeout=5)
    try:
        yield runtime, channel
    finally:
        channel.close()
        server.stop(0)


def test_files_and_uploads_flow(grpc_channel):
    _, channel = grpc_channel
    files_stub = pb2_grpc.FilesServiceStub(channel)
    uploads_stub = pb2_grpc.UploadsServiceStub(channel)
    operations_stub = pb2_grpc.OperationsServiceStub(channel)

    context = pb2.RequestContext(org_id="org-123", user_id="user-123")

    folder = files_stub.CreateFile(
        pb2.CreateFileRequest(
            context=context,
            name="docs",
            parent_id="",
            is_folder=True,
            mime_type="application/vnd.dir",
        )
    )
    assert folder.id

    listing = files_stub.ListFiles(pb2.ListFilesRequest(context=context, parent_id=""))
    assert any(item.id == folder.id for item in listing.files)

    session_resp = uploads_stub.CreateSession(
        pb2.CreateUploadSessionRequest(
            context=context,
            parent_id=folder.id,
            size_bytes=1024,
            chunk_size=1024,
        )
    )
    session_id = session_resp.session.session_id

    append_resp = uploads_stub.AppendChunk(
        pb2.AppendUploadChunkRequest(
            context=context,
            session_id=session_id,
            chunk_id=0,
            offset=0,
            source_node="node1",
            file_name="example.bin",
            chunk_bytes=1024,
        )
    )
    assert append_resp.received_bytes == 1024

    finalize = uploads_stub.Finalize(pb2.FinalizeUploadRequest(context=context, session_id=session_id))
    assert finalize.operation.done
    assert finalize.operation.metadata.resource_id

    fetched_file = files_stub.GetFile(pb2.GetFileRequest(context=context, file_id=finalize.operation.metadata.resource_id))
    assert fetched_file.versions, "expected versions to be populated"

    fetched = operations_stub.Get(pb2.GetOperationRequest(context=context, operation_id=finalize.operation.operation_id))
    assert fetched.operation_id == finalize.operation.operation_id
    assert fetched.done

    download_chunks = list(
        uploads_stub.DownloadChunks(
            pb2.DownloadChunkRequest(
                context=context,
                file_id=finalize.operation.metadata.resource_id,
                offset=0,
            )
        )
    )
    assert download_chunks, "expected at least one download chunk"
    assert download_chunks[-1].eof
    assert sum(len(chunk.data) for chunk in download_chunks) == 1024

    partial = list(
        uploads_stub.DownloadChunks(
            pb2.DownloadChunkRequest(
                context=context,
                file_id=finalize.operation.metadata.resource_id,
                offset=512,
                length=256,
            )
        )
    )
    assert partial[-1].eof
    assert sum(len(chunk.data) for chunk in partial) == 256
