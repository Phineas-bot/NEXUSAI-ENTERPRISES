"""Replay anonymized REST/gRPC traces against a target environment."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import grpc
import requests
from cloud_drive.api import control_plane_pb2 as pb2
from cloud_drive.api import control_plane_pb2_grpc as pb2_grpc
from google.protobuf import json_format


@dataclass
class AuthConfig:
    user_id: str
    org_id: str
    roles: list[str]
    token: str | None = None


def _headers(auth: AuthConfig, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": auth.user_id,
        "X-Org-Id": auth.org_id,
    }
    if auth.roles:
        headers["X-User-Roles"] = ",".join(auth.roles)
    if auth.token:
        headers["Authorization"] = f"Bearer {auth.token}"
    if extra:
        headers.update(extra)
    return headers


class GrpcDispatcher:
    """Dispatch replayed RPCs to generated service stubs."""

    _registry = {
        "ObservabilityService.ListDashboards": (
            "ObservabilityService",
            "ListDashboards",
            pb2.ListDashboardsRequest,
        ),
        "ObservabilityService.ListSLOs": (
            "ObservabilityService",
            "ListSLOs",
            pb2.ListSLOsRequest,
        ),
        "ObservabilityService.UpsertSLO": (
            "ObservabilityService",
            "UpsertSLO",
            pb2.UpsertSLORequest,
        ),
        "ObservabilityService.DeleteSLO": (
            "ObservabilityService",
            "DeleteSLO",
            pb2.DeleteSLORequest,
        ),
    }

    def __init__(self, target: str | None, auth: AuthConfig):
        self._target = target
        self._auth = auth
        self._channel: grpc.Channel | None = None
        self._stubs: dict[str, Any] = {}

    def _ensure_channel(self) -> None:
        if self._channel is None and self._target:
            self._channel = grpc.insecure_channel(self._target)
            self._stubs["ObservabilityService"] = pb2_grpc.ObservabilityServiceStub(self._channel)

    def call(
        self,
        method: str,
        payload: dict[str, Any] | None,
        metadata: dict[str, str] | None,
    ) -> None:
        if not self._target:
            print(f"[grpc] skipping {method}: no target configured")
            return
        if method not in self._registry:
            print(f"[grpc] unsupported method {method}")
            return
        self._ensure_channel()
        service, rpc_name, request_cls = self._registry[method]
        stub = self._stubs.get(service)
        if stub is None:
            print(f"[grpc] no stub for service {service}")
            return

        message = request_cls()
        payload = dict(payload or {})
        payload.setdefault(
            "context",
            {
                "orgId": self._auth.org_id,
                "userId": self._auth.user_id,
                "scopes": self._auth.roles,
            },
        )
        json_format.ParseDict(payload, message)
        md = list(metadata.items()) if metadata else None
        response = getattr(stub, rpc_name)(message, metadata=md)
        print(f"[grpc] {method} -> ok ({response.__class__.__name__})")

    def close(self) -> None:
        if self._channel:
            self._channel.close()


def _load_trace(path: Path) -> Iterable[dict[str, Any]]:
    text = path.read_text().strip()
    if not text:
        return []
    if text.lstrip().startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line]


def replay(
    rest_base: str,
    trace: Iterable[dict[str, Any]],
    speedup: float,
    auth: AuthConfig,
    grpc_dispatcher: GrpcDispatcher,
) -> None:
    base = rest_base.rstrip('/')
    for entry in trace:
        delay_ms = entry.get("delay_ms", 0)
        if delay_ms:
            time.sleep(max(delay_ms / 1000 / speedup, 0))

        if rpc := entry.get("rpc"):
            grpc_dispatcher.call(rpc, entry.get("request"), entry.get("metadata"))
            continue

        method = entry.get("method", "GET").upper()
        path = entry.get("path", "/")
        body = entry.get("body")
        headers = _headers(auth, entry.get("headers"))
        url = f"{base}{path}"
        resp = requests.request(method, url, headers=headers, json=body, timeout=30)
        print(f"{method} {path} -> {resp.status_code}")
        if resp.status_code >= 500:
            print(resp.text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay CloudSim traffic traces")
    parser.add_argument("trace", type=Path, help="Path to JSON trace file")
    parser.add_argument("--rest-base", default="http://localhost:8000")
    parser.add_argument("--speedup", type=float, default=1.0, help="Delay divisor")
    parser.add_argument("--grpc-addr", default=os.environ.get("CLOUDSIM_GRPC_ADDR"))
    parser.add_argument("--user-id", default="user-demo")
    parser.add_argument("--org-id", default="org-demo")
    parser.add_argument("--user-roles", default="ops.admin")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    trace = _load_trace(args.trace)
    auth = AuthConfig(
        user_id=args.user_id,
        org_id=args.org_id,
        roles=[role.strip() for role in args.user_roles.split(",") if role.strip()],
        token=os.environ.get("AUTH_TOKEN"),
    )
    grpc_dispatcher = GrpcDispatcher(args.grpc_addr, auth)
    try:
        replay(args.rest_base, trace, args.speedup, auth, grpc_dispatcher)
    finally:
        grpc_dispatcher.close()


if __name__ == "__main__":
    main()
