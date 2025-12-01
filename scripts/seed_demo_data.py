"""Seed CloudSim with real sample files and optional gRPC observability data."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import grpc
import requests
from cloud_drive.api import control_plane_pb2 as pb2
from cloud_drive.api import control_plane_pb2_grpc as pb2_grpc

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "sample_data"


@dataclass
class AuthConfig:
    user_id: str
    org_id: str
    roles: list[str]
    token: str | None = None


def _headers(auth: AuthConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": auth.user_id,
        "X-Org-Id": auth.org_id,
    }
    if auth.roles:
        headers["X-User-Roles"] = ",".join(auth.roles)
    if auth.token:
        headers["Authorization"] = f"Bearer {auth.token}"
    return headers


def _rest_request(method: str, rest_base: str, path: str, auth: AuthConfig, **kwargs) -> requests.Response:
    url = f"{rest_base.rstrip('/')}{path}"
    headers = _headers(auth)
    headers.update(kwargs.pop("headers", {}))
    resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


def _discover_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        raise FileNotFoundError(f"Sample data directory not found: {data_dir}")
    return [path for path in sorted(data_dir.rglob("*")) if path.is_file()]


def _chunk_slices(total: int, chunk_size: int) -> Iterable[tuple[int, int]]:
    chunk_size = max(1, chunk_size)
    offset = 0
    while offset < total:
        yield offset, min(chunk_size, total - offset)
        offset += chunk_size


def _upload_file(
    rest_base: str,
    auth: AuthConfig,
    parent_id: str,
    source_node: str,
    file_path: Path,
    preferred_chunk: int,
) -> None:
    payload = file_path.read_bytes()
    session_request = {
        "parent_id": parent_id,
        "size_bytes": len(payload),
        "chunk_size": preferred_chunk if preferred_chunk > 0 else None,
        "client_metadata": {"seed_source": str(file_path)},
    }
    session_summary = _rest_request("post", rest_base, "/uploads:sessions", auth, json=session_request).json()
    session_id = session_summary["session_id"]
    effective_chunk = int(session_summary.get("chunk_size") or preferred_chunk or 512 * 1024)

    print(f" -> session {session_id} using chunk_size={effective_chunk}")
    for chunk_id, (offset, size) in enumerate(_chunk_slices(len(payload), effective_chunk)):
        chunk_request = {
            "session_id": session_id,
            "source_node": source_node,
            "file_name": file_path.name,
            "chunk_bytes": size,
            "chunk_id": chunk_id,
            "offset": offset,
        }
        _rest_request("post", rest_base, "/uploads:chunk", auth, json=chunk_request)

    _rest_request("post", rest_base, f"/uploads:finalize/{session_id}", auth)


def _seed_observability(grpc_addr: str, auth: AuthConfig, folder_name: str) -> None:
    context = pb2.RequestContext(org_id=auth.org_id, user_id=auth.user_id, scopes=auth.roles or ["ops.admin"])
    channel = grpc.insecure_channel(grpc_addr)
    stub = pb2_grpc.ObservabilityServiceStub(channel)
    slo = pb2.SLO(
        name=f"{folder_name}-upload-latency",
        metric="upload.latency.p95",
        threshold=1800,
        comparator="<",
        window_minutes=60,
    )
    stub.UpsertSLO(pb2.UpsertSLORequest(context=context, slo=slo))
    stub.ListDashboards(pb2.ListDashboardsRequest(context=context))
    channel.close()


def seed(
    rest_base: str,
    data_files: Sequence[Path],
    auth: AuthConfig,
    source_node: str,
    preferred_chunk: int,
    grpc_addr: str | None,
    env_label: str,
) -> None:
    folder_name = f"{env_label}-seed"
    folder_resp = _rest_request("post", rest_base, "/folders", auth, json={"name": folder_name, "parent_id": None})
    folder_id = folder_resp.json()["id"]
    print(f"Created folder {folder_name} ({folder_id})")

    for path in data_files:
        print(f"Uploading {path.name} ({path.stat().st_size} bytes)")
        _upload_file(rest_base, auth, folder_id, source_node, path, preferred_chunk)

    if grpc_addr:
        print(f"Seeding observability SLOs via gRPC at {grpc_addr}")
        _seed_observability(grpc_addr, auth, folder_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed CloudSim demo data")
    parser.add_argument("--env", default="local", help="Label used in folder naming")
    parser.add_argument("--rest-base", default="http://localhost:8000", help="REST base URL")
    parser.add_argument("--grpc-addr", default=os.environ.get("CLOUDSIM_GRPC_ADDR"), help="Optional gRPC addr")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Directory of sample files")
    parser.add_argument("--source-node", default="rest-node", help="Node name recorded in chunk metadata")
    parser.add_argument("--chunk-size", type=int, default=512 * 1024, help="Preferred chunk size in bytes")
    parser.add_argument("--user-id", default="user-seeder")
    parser.add_argument("--org-id", default="org-demo")
    parser.add_argument("--user-roles", default="ops.admin", help="Comma separated roles for auth headers")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    auth = AuthConfig(
        user_id=args.user_id,
        org_id=args.org_id,
        roles=[role.strip() for role in args.user_roles.split(",") if role.strip()],
        token=os.environ.get("AUTH_TOKEN"),
    )
    files = _discover_files(args.data_dir)
    if not files:
        raise SystemExit(f"No files found in {args.data_dir}")

    seed(
        args.rest_base,
        files,
        auth,
        args.source_node,
        args.chunk_size,
        args.grpc_addr,
        args.env,
    )


if __name__ == "__main__":
    main()
