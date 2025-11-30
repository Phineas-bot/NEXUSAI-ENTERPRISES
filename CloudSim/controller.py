from __future__ import annotations

import shlex
import random
from collections import deque
from dataclasses import dataclass, asdict
from typing import Deque, Dict, List, Optional, Tuple

from simulator import Simulator
from storage_virtual_network import StorageVirtualNetwork
from storage_virtual_node import StorageVirtualNode


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

    def __init__(self, tick_interval: float = 0.005, event_history: int = 200):
        self.simulator = Simulator()
        self.network = StorageVirtualNetwork(self.simulator, tick_interval=tick_interval)
        self._events: Deque[Dict[str, object]] = deque(maxlen=event_history)
        self.network.register_observer(self._record_event)
        self._rng = random.Random()

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
        zone: Optional[str] = None,
    ) -> StorageVirtualNode:
        if node_id in self.network.nodes:
            raise ValueError(f"Node '{node_id}' already exists")
        zone = zone or self._random_zone()
        node = StorageVirtualNode(
            node_id,
            cpu_capacity=cpu_capacity,
            memory_capacity=memory_capacity,
            storage_capacity=storage_gb,
            bandwidth=bandwidth_mbps,
            zone=zone,
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
                    zone=node.zone,
                    replicas=self.network.get_replica_parent(node_id),
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
            "zone": node.zone,
            "replica_parent": self.network.get_replica_parent(node_id),
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
