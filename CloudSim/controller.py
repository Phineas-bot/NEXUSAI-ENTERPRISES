from __future__ import annotations

import os
import shlex
import random
import sys
from collections import deque
from dataclasses import dataclass, asdict
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from simulator import Simulator
from state_store import CloudSimStateStore
from storage_virtual_network import DemandScalingConfig, StorageVirtualNetwork
from storage_virtual_node import FileChunk, FileTransfer, StorageVirtualNode, TransferStatus


ZONE_CATALOG = (
    "us-east-1a",
    "us-east-1b",
    "us-east-2a",
    "us-west-1a",
    "us-west-2b",
    "eu-central-1a",
    "eu-west-1b",
    "ap-south-1a",
    "ap-northeast-1c",
    "sa-east-1a",
)


@dataclass
class NodeStatus:
    node_id: str
    online: bool
    storage_used: int
    storage_total: int
    bandwidth_bps: int
    zone: Optional[str]
    replicas: Optional[str] = None


class CloudSimController:
    """Stateful helper exposing imperative control over StorageVirtualNetwork."""

    def __init__(
        self,
        tick_interval: float = 0.005,
        event_history: int = 200,
        *,
        enable_persistence: bool = False,
        state_path: Optional[str] = None,
    ):
        self._tick_interval = tick_interval
        self._event_history_limit = event_history
        base_scaling = DemandScalingConfig(
            enabled=True,
            storage_utilization_threshold=0.7,
            bandwidth_utilization_threshold=0.9,
            auto_replication_enabled=True,
            min_replicas_per_root=2,
            max_replicas_per_root=3,
            replica_seed_limit=None,
        )
        self._base_scaling_template = asdict(base_scaling)
        self._rng = random.Random()
        self._restoring_state = False
        self._persistence_enabled = enable_persistence
        self._state_store: Optional[CloudSimStateStore] = None
        self._setup_runtime()

        if self._persistence_enabled:
            default_path = state_path or os.path.join(os.path.dirname(__file__), "cloudsim_state.json")
            self._state_store = CloudSimStateStore(default_path)
            snapshot = self._state_store.load()
            if snapshot:
                self._restore_state(snapshot)
            else:
                self._persist_state()

    # Event handling -----------------------------------------------------
    def _record_event(self, event: Dict[str, object]) -> None:
        self._events.append(event)

    def recent_events(self, limit: int = 10) -> List[Dict[str, object]]:
        return list(self._events)[-limit:]

    def _setup_runtime(self, scaling_override: Optional[DemandScalingConfig] = None) -> None:
        config = scaling_override or DemandScalingConfig(**self._base_scaling_template)
        self.simulator = Simulator()
        self.network = StorageVirtualNetwork(
            self.simulator,
            tick_interval=self._tick_interval,
            scaling_config=config,
        )
        self._events = deque(maxlen=self._event_history_limit)
        self.network.register_observer(self._record_event)

    # Node management ----------------------------------------------------
    def add_node(
        self,
        node_id: str,
        *,
        storage_gb: Optional[float] = None,
        bandwidth_mbps: int = 1000,
        cpu_capacity: int = 8,
        memory_capacity: int = 32,
        root_id: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> StorageVirtualNode:
        if node_id in self.network.nodes:
            raise ValueError(f"Node '{node_id}' already exists")
        zone = zone or self._random_zone()
        if storage_gb is None:
            storage_mb = self._rng.randint(30, 50)
            storage_gb = max(storage_mb / 1024.0, 0.03)
        node = StorageVirtualNode(
            node_id,
            cpu_capacity=cpu_capacity,
            memory_capacity=memory_capacity,
            storage_capacity=storage_gb,
            bandwidth=bandwidth_mbps,
            zone=zone,
        )
        self.network.add_node(node, root_id=root_id)
        self._persist_state()
        return node

    def remove_node(self, node_id: str) -> bool:
        removed = self.network.remove_node(node_id)
        if removed:
            self._persist_state()
        return removed

    def list_node_status(self, include_replicas: bool = False) -> List[NodeStatus]:
        rows: List[NodeStatus] = []
        for node_id, node in self.network.nodes.items():
            replica_parent = self.network.get_replica_parent(node_id)
            if not include_replicas and replica_parent:
                continue
            rows.append(
                NodeStatus(
                    node_id=node_id,
                    online=node_id not in self.network.failed_nodes,
                    storage_used=node.used_storage,
                    storage_total=node.total_storage,
                    bandwidth_bps=node.bandwidth,
                    zone=node.zone,
                    replicas=replica_parent,
                )
            )
        return rows

    def get_clusters(self) -> Dict[str, List[str]]:
        return {root: sorted(nodes) for root, nodes in self.network.cluster_nodes.items()}

    # Topology -----------------------------------------------------------
    def connect_nodes(
        self,
        node_a: str,
        node_b: str,
        bandwidth_mbps: Optional[int] = None,
        latency_ms: Optional[float] = None,
    ) -> bool:
        if bandwidth_mbps is None or latency_ms is None:
            inferred_bw, inferred_latency = self._auto_link_profile(node_a, node_b)
            if bandwidth_mbps is None:
                bandwidth_mbps = inferred_bw
            if latency_ms is None:
                latency_ms = inferred_latency
        success = self.network.connect_nodes(node_a, node_b, bandwidth_mbps, latency_ms)
        if success:
            self._persist_state()
        return success

    def disconnect_nodes(self, node_a: str, node_b: str) -> bool:
        if node_a not in self.network.nodes or node_b not in self.network.nodes:
            return False
        self.network.nodes[node_a].connections.pop(node_b, None)
        self.network.nodes[node_b].connections.pop(node_a, None)
        key = (node_a, node_b)
        reverse = (node_b, node_a)
        self.network.failed_links.discard(key)
        self.network.failed_links.discard(reverse)
        self._persist_state()
        return True

    # Transfers ----------------------------------------------------------
    def initiate_transfer(self, source: str, target: str, file_name: str, size_bytes: int):
        transfer = self.network.initiate_file_transfer(source, target, file_name, size_bytes)
        if not transfer:
            raise RuntimeError("Transfer could not be started (insufficient capacity or invalid route)")
        return transfer

    def push_file(
        self,
        source_node_id: str,
        file_name: str,
        size_bytes: int,
        *,
        prefer_local: bool = False,
    ) -> Tuple[str, FileTransfer]:
        result = self.network.ingest_file(source_node_id, file_name, size_bytes, prefer_local=prefer_local)
        if not result:
            mode = "locally" if prefer_local else "into the network"
            raise RuntimeError(f"Unable to store '{file_name}' {mode}; insufficient capacity or routing")
        target_id, transfer = result
        return target_id, transfer

    def store_file_locally(self, node_id: str, file_name: str, size_bytes: int) -> FileTransfer:
        transfer = self.network.store_local_file(node_id, file_name, size_bytes)
        if not transfer:
            raise RuntimeError(f"Node '{node_id}' lacks capacity to store '{file_name}' locally")
        return transfer

    def pull_file(self, target_node_id: str, file_name: str) -> FileTransfer:
        if target_node_id not in self.network.nodes:
            raise ValueError(f"Target node '{target_node_id}' does not exist")
        manifest = self.network.get_file_manifest(file_name)
        if manifest:
            transfer = self.network.assemble_file(file_name, target_node_id)
            if not transfer:
                raise RuntimeError(f"Unable to assemble '{file_name}' for delivery")
            return transfer
        matches = self.network.locate_file(file_name)
        if not matches:
            raise RuntimeError(f"No stored copy of '{file_name}' found in the network")

        for node_id, transfer in matches:
            if node_id == target_node_id:
                return transfer

        for node_id, transfer in matches:
            replica_transfer = self.network.initiate_replica_transfer(node_id, target_node_id, transfer.file_id)
            if replica_transfer:
                return replica_transfer
        raise RuntimeError(f"Unable to route '{file_name}' to {target_node_id}; no reachable source found")

    def run_until_idle(self) -> None:
        self.simulator.run()
        self._persist_state()

    def run_for(self, duration: float) -> None:
        self.simulator.run(until=self.simulator.now + duration)
        self._persist_state()

    # Failure injection --------------------------------------------------
    def fail_node(self, node_id: str) -> bool:
        result = self.network.fail_node(node_id)
        if result:
            self._persist_state()
        return result

    def restore_node(self, node_id: str) -> None:
        self.network.restore_node(node_id)
        self._persist_state()

    def fail_link(self, node_a: str, node_b: str) -> bool:
        result = self.network.fail_link(node_a, node_b)
        if result:
            self._persist_state()
        return result

    def restore_link(self, node_a: str, node_b: str) -> None:
        self.network.restore_link(node_a, node_b)
        self._persist_state()

    # Inspection ---------------------------------------------------------
    def get_transfer_summary(self) -> List[Dict[str, object]]:
        summaries: List[Dict[str, object]] = []
        for source_id, transfers in self.network.transfer_operations.items():
            for transfer in transfers.values():
                summaries.append(
                    {
                        "file": transfer.file_name,
                        "source": source_id,
                        "target": transfer.target_node,
                        "status": transfer.status.name,
                        "size_bytes": transfer.total_size,
                        "chunks": len(transfer.chunks),
                        "created_at": transfer.created_at,
                        "completed_at": transfer.completed_at,
                    }
                )
        return summaries

    def get_node_info(self, node_id: str) -> Optional[Dict[str, object]]:
        node = self.network.nodes.get(node_id)
        if not node:
            return None
        telemetry = self.network.get_node_telemetry(node_id)
        info = {
            "node_id": node_id,
            "online": node_id not in self.network.failed_nodes,
            "neighbors": list(node.connections.keys()),
            "used_storage": node.used_storage,
            "total_storage": node.total_storage,
            "available_storage": max(0, node.total_storage - node.used_storage),
            "bandwidth": node.bandwidth,
            "zone": node.zone,
            "replica_parent": self.network.get_replica_parent(node_id),
            "replica_children": self.network.get_replica_children(node_id),
            "stored_files": [
                {
                    "file_id": transfer.file_id,
                    "file_name": transfer.file_name,
                    "size_bytes": transfer.total_size,
                    "completed_at": transfer.completed_at,
                }
                for transfer in node.stored_files.values()
            ],
            "active_transfers": [
                {
                    "file_id": transfer.file_id,
                    "status": transfer.status.name,
                    "size_bytes": transfer.total_size,
                }
                for transfer in node.active_transfers.values()
            ],
        }
        if telemetry:
            info["telemetry"] = asdict(telemetry)
        return info

    def _random_zone(self) -> str:
        return self._rng.choice(ZONE_CATALOG)

    @staticmethod
    def _zone_region(zone: Optional[str]) -> Optional[str]:
        if not zone:
            return None
        tokens = zone.split("-")
        if len(tokens) < 3:
            return zone
        return "-".join(tokens[:3])

    def _auto_link_profile(self, node_a: str, node_b: str) -> Tuple[int, float]:
        node_obj_a = self.network.nodes.get(node_a)
        node_obj_b = self.network.nodes.get(node_b)
        if not node_obj_a or not node_obj_b:
            return 1000, 1.0

        zone_a = node_obj_a.zone
        zone_b = node_obj_b.zone
        same_zone = zone_a and zone_b and zone_a == zone_b
        region_a = self._zone_region(zone_a)
        region_b = self._zone_region(zone_b)
        same_region = bool(region_a and region_b and region_a == region_b)

        rng = self._rng
        if same_zone:
            bandwidth = rng.randint(1800, 2500)
            latency = round(rng.uniform(0.2, 0.8), 2)
        elif same_region:
            bandwidth = rng.randint(900, 1600)
            latency = round(rng.uniform(2.0, 7.0), 2)
        else:
            bandwidth = rng.randint(300, 900)
            latency = round(rng.uniform(20.0, 80.0), 2)
        return bandwidth, latency

    # Snapshot management -----------------------------------------------
    def save_snapshot(self, path: Optional[str] = None) -> str:
        snapshot = self._snapshot_state()
        if path:
            store = CloudSimStateStore(path)
            store.save(snapshot)
            return store.path
        if not self._state_store:
            raise RuntimeError("Persistence is disabled; provide a destination path")
        self._state_store.save(snapshot)
        return self._state_store.path

    def load_snapshot(self, path: Optional[str] = None) -> bool:
        store = None
        if path:
            store = CloudSimStateStore(path)
            self._state_store = store
            self._persistence_enabled = True
        else:
            store = self._state_store
        if not store:
            raise RuntimeError("No persistence backend configured")
        snapshot = store.load()
        if not snapshot:
            return False
        self._restore_state(snapshot, reset_runtime=True)
        self._persist_state()
        return True

    def reset_state(self, *, clear_saved: bool = False) -> None:
        self._setup_runtime()
        if clear_saved and self._state_store:
            self._state_store.clear()
        self._persist_state()

    def get_state_path(self) -> Optional[str]:
        if not self._state_store:
            return None
        return self._state_store.path

    # Persistence -------------------------------------------------------
    def _persist_state(self) -> None:
        if not self._persistence_enabled or self._restoring_state or not self._state_store:
            return
        try:
            self._state_store.save(self._snapshot_state())
        except OSError as exc:
            print(f"[cloudsim] Failed to persist state: {exc}", file=sys.stderr)

    def _snapshot_state(self) -> Dict[str, Any]:
        network = self.network
        nodes_payload: List[Dict[str, Any]] = []
        for node_id, node in network.nodes.items():
            node_payload = {
                "node_id": node_id,
                "cpu_capacity": node.cpu_capacity,
                "memory_capacity": node.memory_capacity,
                "storage_gb": max(1, int(round(node.total_storage / (1024 ** 3)))) ,
                "bandwidth_mbps": max(1, int(max(1, node.bandwidth) / 1_000_000)),
                "zone": node.zone,
                "root_id": network.node_roots.get(node_id, node_id),
                "replica_parent": network.get_replica_parent(node_id),
                "failed": node_id in network.failed_nodes,
                "stored_files": self._serialize_node_files(node),
            }
            nodes_payload.append(node_payload)

        links_payload: List[Dict[str, Any]] = []
        seen_links: Set[Tuple[str, str]] = set()
        for node_id, node in network.nodes.items():
            for neighbor_id, bandwidth_bps in node.connections.items():
                if neighbor_id not in network.nodes:
                    continue
                link_key = tuple(sorted((node_id, neighbor_id)))
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)
                latency = (
                    node.link_latencies.get(neighbor_id)
                    or network.link_latency_ms.get((node_id, neighbor_id))
                    or 0.0
                )
                links_payload.append(
                    {
                        "a": link_key[0],
                        "b": link_key[1],
                        "bandwidth_mbps": max(1, int(max(1, bandwidth_bps) / 1_000_000)),
                        "latency_ms": latency,
                    }
                )

        snapshot = {
            "schema_version": 1,
            "simulator": {"now": self.simulator.now},
            "scaling_config": asdict(network.scaling),
            "routing_strategy": network.routing_strategy,
            "nodes": nodes_payload,
            "links": links_payload,
            "failed_nodes": list(network.failed_nodes),
            "failed_links": [list(link) for link in network.failed_links],
            "replica_parents": dict(network._replica_parents),
            "node_roots": dict(network.node_roots),
            "clusters": {root: sorted(nodes) for root, nodes in network.cluster_nodes.items()},
            "events": list(self._events),
        }
        return snapshot

    def _serialize_node_files(self, node: StorageVirtualNode) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for transfer in node.stored_files.values():
            metadata = node.disk.get_file_metadata(transfer.file_id) if hasattr(node, "disk") else None
            entries.append(self._serialize_transfer(transfer, metadata))
        return entries

    def _serialize_transfer(self, transfer: FileTransfer, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "file_id": transfer.file_id,
            "file_name": transfer.file_name,
            "total_size": transfer.total_size,
            "status": transfer.status.name,
            "created_at": transfer.created_at,
            "completed_at": transfer.completed_at,
            "is_retrieval": transfer.is_retrieval,
            "backing_file_id": transfer.backing_file_id,
            "path": (metadata or {}).get("path"),
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "size": chunk.size,
                    "checksum": chunk.checksum,
                    "status": chunk.status.name,
                    "stored_node": chunk.stored_node,
                }
                for chunk in transfer.chunks
            ],
        }

    def _restore_state(self, snapshot: Dict[str, Any], *, reset_runtime: bool = False) -> None:
        self._restoring_state = True
        try:
            if reset_runtime:
                self._setup_runtime()
            scaling_config = snapshot.get("scaling_config") or {}
            self.network.scaling = DemandScalingConfig(**scaling_config)
            simulator_now = snapshot.get("simulator", {}).get("now", 0.0)
            self.simulator._clock = simulator_now  # type: ignore[attr-defined]
            routing_strategy = snapshot.get("routing_strategy")
            if routing_strategy:
                self.network.routing_strategy = routing_strategy
            auto_flag = self.network.scaling.auto_replication_enabled
            self.network.scaling.auto_replication_enabled = False
            for node_payload in snapshot.get("nodes", []):
                node = StorageVirtualNode(
                    node_payload["node_id"],
                    cpu_capacity=node_payload.get("cpu_capacity", 8),
                    memory_capacity=node_payload.get("memory_capacity", 32),
                    storage_capacity=node_payload.get("storage_gb", 500),
                    bandwidth=node_payload.get("bandwidth_mbps", 1000),
                    zone=node_payload.get("zone"),
                )
                root_id = node_payload.get("root_id")
                self.network.add_node(node, root_id=root_id, suppress_replica_coverage=True)
                self._restore_node_files(node, node_payload.get("stored_files", []))
            self.network.scaling.auto_replication_enabled = auto_flag

            replica_parents = snapshot.get("replica_parents") or {}
            self.network._replica_parents = dict(replica_parents)
            node_roots = snapshot.get("node_roots") or {}
            if node_roots:
                self.network.node_roots.update(node_roots)
            clusters = snapshot.get("clusters") or {}
            if clusters:
                self.network.cluster_nodes.clear()
                for root, members in clusters.items():
                    self.network.cluster_nodes[root] = set(members)

            self._restore_links(snapshot.get("links", []))
            failed_nodes = set(snapshot.get("failed_nodes", []))
            self.network.failed_nodes = failed_nodes
            failed_links = snapshot.get("failed_links", [])
            self.network.failed_links = {tuple(pair) for pair in failed_links}

            self._events.clear()
            for event in snapshot.get("events", []):
                self._events.append(event)
        finally:
            self._restoring_state = False

    def _restore_links(self, links: List[Dict[str, Any]]) -> None:
        for link in links:
            node_a = link.get("a")
            node_b = link.get("b")
            if not node_a or not node_b:
                continue
            if node_a not in self.network.nodes or node_b not in self.network.nodes:
                continue
            bandwidth_mbps = max(1, int(link.get("bandwidth_mbps", 1)))
            latency = link.get("latency_ms", 0.0)
            bandwidth_bps = bandwidth_mbps * 1_000_000
            self.network.nodes[node_a].connections[node_b] = bandwidth_bps
            self.network.nodes[node_b].connections[node_a] = bandwidth_bps
            self.network.nodes[node_a].link_latencies[node_b] = latency
            self.network.nodes[node_b].link_latencies[node_a] = latency
            self.network.link_latency_ms[(node_a, node_b)] = latency
            self.network.link_latency_ms[(node_b, node_a)] = latency

    def _restore_node_files(self, node: StorageVirtualNode, stored_files: List[Dict[str, Any]]) -> None:
        for entry in stored_files:
            chunks_payload = entry.get("chunks", [])
            if not chunks_payload:
                chunks_payload = [
                    {
                        "chunk_id": 0,
                        "size": entry.get("total_size", 0),
                        "checksum": None,
                        "status": "COMPLETED",
                        "stored_node": node.node_id,
                    }
                ]
            chunks = [
                FileChunk(
                    chunk_id=chunk_payload.get("chunk_id", idx),
                    size=chunk_payload.get("size", 0),
                    checksum=chunk_payload.get("checksum"),
                    status=self._status_from_name(chunk_payload.get("status", "COMPLETED")),
                    stored_node=chunk_payload.get("stored_node"),
                )
                for idx, chunk_payload in enumerate(chunks_payload)
            ]
            transfer = FileTransfer(
                file_id=entry["file_id"],
                file_name=entry.get("file_name", entry["file_id"]),
                total_size=entry.get("total_size", 0),
                chunks=chunks,
                status=self._status_from_name(entry.get("status", "COMPLETED")),
                created_at=entry.get("created_at", 0.0),
                completed_at=entry.get("completed_at"),
                is_retrieval=entry.get("is_retrieval", False),
                backing_file_id=entry.get("backing_file_id"),
            )
            transfer.status = TransferStatus.COMPLETED
            transfer.completed_at = transfer.completed_at or self.simulator.now
            node.stored_files[transfer.file_id] = transfer
            path = entry.get("path") or f"/{node.node_id}/{transfer.file_name}"
            node.disk.reserve_file(transfer.file_id, transfer.total_size, path=path)
            for chunk in chunks:
                node.disk.write_chunk(transfer.file_id, chunk.chunk_id, None, chunk.size)
            node.total_requests_processed += 1
            node.total_data_transferred += transfer.total_size

    @staticmethod
    def _status_from_name(value: str) -> TransferStatus:
        try:
            return TransferStatus[value]
        except KeyError:
            return TransferStatus.COMPLETED


def parse_size(value: str) -> int:
    value = value.strip().lower()
    if value.endswith("gb"):
        return int(float(value[:-2]) * 1024 * 1024 * 1024)
    if value.endswith("mb"):
        return int(float(value[:-2]) * 1024 * 1024)
    if value.endswith("kb"):
        return int(float(value[:-2]) * 1024)
    if value.endswith("b"):
        return int(float(value[:-1]))
    return int(float(value))
