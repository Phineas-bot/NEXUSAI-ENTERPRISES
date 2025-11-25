from __future__ import annotations

import shlex
from collections import deque
from dataclasses import dataclass, asdict
from typing import Deque, Dict, List, Optional

from simulator import Simulator
from storage_virtual_network import StorageVirtualNetwork
from storage_virtual_node import StorageVirtualNode


@dataclass
class NodeStatus:
    node_id: str
    online: bool
    storage_used: int
    storage_total: int
    bandwidth_bps: int
    replicas: Optional[str] = None


class CloudSimController:
    """Stateful helper exposing imperative control over StorageVirtualNetwork."""

    def __init__(self, tick_interval: float = 0.005, event_history: int = 200):
        self.simulator = Simulator()
        self.network = StorageVirtualNetwork(self.simulator, tick_interval=tick_interval)
        self._events: Deque[Dict[str, object]] = deque(maxlen=event_history)
        self.network.register_observer(self._record_event)

    # Event handling -----------------------------------------------------
    def _record_event(self, event: Dict[str, object]) -> None:
        self._events.append(event)

    def recent_events(self, limit: int = 10) -> List[Dict[str, object]]:
        return list(self._events)[-limit:]

    # Node management ----------------------------------------------------
    def add_node(
        self,
        node_id: str,
        *,
        storage_gb: int = 500,
        bandwidth_mbps: int = 1000,
        cpu_capacity: int = 8,
        memory_capacity: int = 32,
        root_id: Optional[str] = None,
    ) -> StorageVirtualNode:
        if node_id in self.network.nodes:
            raise ValueError(f"Node '{node_id}' already exists")
        node = StorageVirtualNode(
            node_id,
            cpu_capacity=cpu_capacity,
            memory_capacity=memory_capacity,
            storage_capacity=storage_gb,
            bandwidth=bandwidth_mbps,
        )
        self.network.add_node(node, root_id=root_id)
        return node

    def remove_node(self, node_id: str) -> bool:
        return self.network.remove_node(node_id)

    def list_node_status(self) -> List[NodeStatus]:
        rows: List[NodeStatus] = []
        for node_id, node in self.network.nodes.items():
            rows.append(
                NodeStatus(
                    node_id=node_id,
                    online=node_id not in self.network.failed_nodes,
                    storage_used=node.used_storage,
                    storage_total=node.total_storage,
                    bandwidth_bps=node.bandwidth,
                    replicas=self.network.get_replica_parent(node_id),
                )
            )
        return rows

    def get_clusters(self) -> Dict[str, List[str]]:
        return {root: sorted(nodes) for root, nodes in self.network.cluster_nodes.items()}

    # Topology -----------------------------------------------------------
    def connect_nodes(self, node_a: str, node_b: str, bandwidth_mbps: int = 1000, latency_ms: float = 1.0) -> bool:
        return self.network.connect_nodes(node_a, node_b, bandwidth_mbps, latency_ms)

    def disconnect_nodes(self, node_a: str, node_b: str) -> bool:
        if node_a not in self.network.nodes or node_b not in self.network.nodes:
            return False
        self.network.nodes[node_a].connections.pop(node_b, None)
        self.network.nodes[node_b].connections.pop(node_a, None)
        key = (node_a, node_b)
        reverse = (node_b, node_a)
        self.network.failed_links.discard(key)
        self.network.failed_links.discard(reverse)
        return True

    # Transfers ----------------------------------------------------------
    def initiate_transfer(self, source: str, target: str, file_name: str, size_bytes: int):
        transfer = self.network.initiate_file_transfer(source, target, file_name, size_bytes)
        if not transfer:
            raise RuntimeError("Transfer could not be started (insufficient capacity or invalid route)")
        return transfer

    def run_until_idle(self) -> None:
        self.simulator.run()

    def run_for(self, duration: float) -> None:
        self.simulator.run(until=self.simulator.now + duration)

    # Failure injection --------------------------------------------------
    def fail_node(self, node_id: str) -> bool:
        return self.network.fail_node(node_id)

    def restore_node(self, node_id: str) -> None:
        self.network.restore_node(node_id)

    def fail_link(self, node_a: str, node_b: str) -> bool:
        return self.network.fail_link(node_a, node_b)

    def restore_link(self, node_a: str, node_b: str) -> None:
        self.network.restore_link(node_a, node_b)

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
            "bandwidth": node.bandwidth,
            "replica_parent": self.network.get_replica_parent(node_id),
        }
        if telemetry:
            info["telemetry"] = asdict(telemetry)
        return info


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
