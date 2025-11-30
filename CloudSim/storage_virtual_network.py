from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import hashlib
from collections import defaultdict, deque
import heapq
import math

from storage_virtual_node import (
    StorageVirtualNode,
    FileTransfer,
    FileChunk,
    TransferStatus,
)
from simulator import Simulator

ChunkKey = Tuple[str, str, str, int]


@dataclass
class ActiveChunk:
    source: str
    target: str
    transfer: FileTransfer
    chunk: FileChunk
    remaining_bytes: float
    path: List[str]
    hop_index: int = 0
    os_pid: Optional[int] = None

    def current_hop_nodes(self) -> Tuple[str, str]:
        return self.path[self.hop_index], self.path[self.hop_index + 1]

    def on_last_hop(self) -> bool:
        return self.hop_index >= len(self.path) - 2


@dataclass
class PendingChunkCommit:
    chunk_key: ChunkKey
    source: str
    target: str
    transfer: FileTransfer
    chunk: FileChunk
    completion_time: float
    bandwidth_bps: float


@dataclass
class NodeTelemetry:
    node_id: str
    storage_ratio: float
    bandwidth_ratio: float
    os_memory_ratio: float
    os_failure_delta: int
    used_bytes: int
    reserved_bytes: int
    timestamp: float

@dataclass
class DemandScalingConfig:
    enabled: bool = False
    storage_utilization_threshold: float = 0.8
    bandwidth_utilization_threshold: float = 0.85
    max_replicas_per_root: int = 3
    replica_storage_factor: float = 1.0
    replica_bandwidth_factor: float = 1.0
    os_failure_threshold: Optional[int] = None
    os_memory_utilization_threshold: Optional[float] = None
    trigger_priority: Optional[List[str]] = None
    replica_seed_limit: Optional[int] = None
    auto_replication_enabled: bool = False
    min_replicas_per_root: int = 0


@dataclass
class FileSegment:
    node_id: str
    file_id: str
    size: int
    offset: int


@dataclass
class FileManifest:
    file_name: str
    master_id: str
    total_size: int
    segments: List[FileSegment]
    created_at: float


class StorageVirtualNetwork:
    def __init__(
        self,
        simulator: Simulator,
        tick_interval: float = 0.01,
        scaling_config: Optional[DemandScalingConfig] = None,
        routing_strategy: str = "link_state",
    ):
        self.simulator = simulator
        self.tick_interval = tick_interval
        self.nodes: Dict[str, StorageVirtualNode] = {}
        self.transfer_operations: Dict[str, Dict[str, FileTransfer]] = defaultdict(dict)
        self.transfer_observers: List[Callable[[Dict[str, Any]], None]] = []
        self.scaling = scaling_config or DemandScalingConfig()
        self.routing_strategy = routing_strategy.lower()
        if self.routing_strategy not in {"link_state", "distance_vector"}:
            raise ValueError("routing_strategy must be 'link_state' or 'distance_vector'")

        # Concurrent transfer bookkeeping
        self.active_chunks: Dict[ChunkKey, ActiveChunk] = {}
        self.link_active_chunks: Dict[Tuple[str, str], Set[ChunkKey]] = defaultdict(set)
        self.node_active_chunks: Dict[str, Set[ChunkKey]] = defaultdict(set)
        self.chunk_bandwidths: Dict[ChunkKey, float] = defaultdict(float)
        self._tick_scheduled = False
        self._pending_disk_commits: Dict[ChunkKey, PendingChunkCommit] = {}
        self.node_telemetry: Dict[str, NodeTelemetry] = {}
        self._last_scaling_trigger: Dict[str, str] = {}
        self._replica_parents: Dict[str, str] = {}
        self.file_manifests: Dict[str, FileManifest] = {}
        self.file_manifests_by_id: Dict[str, FileManifest] = {}
        self.segment_manifests: Dict[str, FileManifest] = {}
        self.file_names: Dict[str, str] = {}
        self.file_manifests: Dict[str, "FileManifest"] = {}
        self.file_manifests_by_id: Dict[str, "FileManifest"] = {}

        # Replica/cluster bookkeeping for decentralized scaling
        self.node_roots: Dict[str, str] = {}
        self.cluster_nodes: Dict[str, Set[str]] = defaultdict(set)
        self._cluster_observers: List[Callable[[str, List[str]], None]] = []

        # Network topology and addressing
        self.link_latency_ms: Dict[Tuple[str, str], float] = {}
        self._ip_counter = 1
        self.failed_links: Set[Tuple[str, str]] = set()
        self.failed_nodes: Set[str] = set()
        self._os_failure_baseline: Dict[str, int] = defaultdict(int)
        
    def add_node(
        self,
        node: StorageVirtualNode,
        root_id: Optional[str] = None,
        *,
        suppress_replica_coverage: bool = False,
    ):
        """Add a node to the network"""
        if not getattr(node, "ip_address", None):
            node.ip_address = self._allocate_ip()
        if hasattr(node, "attach_simulator"):
            node.attach_simulator(self.simulator)
        self.nodes[node.node_id] = node
        self._register_node_cluster(node.node_id, root_id)
        self._os_failure_baseline.setdefault(node.node_id, node.os_process_failures)
        self.failed_nodes.discard(node.node_id)
        if not suppress_replica_coverage:
            self._ensure_replica_coverage(node.node_id)
        
    def connect_nodes(self, node1_id: str, node2_id: str, bandwidth: int, latency_ms: float = 1.0):
        """Connect two nodes with specified bandwidth and latency"""
        if node1_id in self.nodes and node2_id in self.nodes:
            self._establish_link(node1_id, node2_id, bandwidth, latency_ms)
            self._mirror_replica_links(node1_id, node2_id, bandwidth, latency_ms)
            return True
        return False

    def remove_node(self, node_id: str) -> bool:
        node = self.nodes.get(node_id)
        if not node:
            return False
        self.fail_node(node_id)
        for neighbor_id in list(node.connections.keys()):
            node.connections.pop(neighbor_id, None)
            if neighbor_id in self.nodes:
                self.nodes[neighbor_id].connections.pop(node_id, None)
        self.nodes.pop(node_id, None)
        root_id = self.node_roots.pop(node_id, None)
        if root_id and root_id in self.cluster_nodes:
            self.cluster_nodes[root_id].discard(node_id)
            if not self.cluster_nodes[root_id]:
                self.cluster_nodes.pop(root_id)
            else:
                self._notify_cluster_observers(root_id)
                self._ensure_replica_coverage(root_id)
        self.failed_nodes.discard(node_id)
        self._replica_parents.pop(node_id, None)
        return True

    def fail_link(self, node1_id: str, node2_id: str) -> bool:
        if node1_id not in self.nodes or node2_id not in self.nodes:
            return False
        link = self._link_key(node1_id, node2_id)
        reverse = self._link_key(node2_id, node1_id)
        if link in self.failed_links:
            return True
        self.failed_links.add(link)
        self.failed_links.add(reverse)
        self._handle_link_failure(node1_id, node2_id)
        return True

    def restore_link(self, node1_id: str, node2_id: str) -> None:
        link = self._link_key(node1_id, node2_id)
        reverse = self._link_key(node2_id, node1_id)
        self.failed_links.discard(link)
        self.failed_links.discard(reverse)
        self._recalculate_link_share(node1_id, node2_id)

    def fail_node(self, node_id: str) -> bool:
        if node_id not in self.nodes:
            return False
        if node_id in self.failed_nodes:
            return True
        self.failed_nodes.add(node_id)
        self.nodes[node_id].network_utilization = 0.0
        self._handle_node_failure(node_id)
        self._ensure_replica_coverage(node_id)
        return True

    def restore_node(self, node_id: str) -> None:
        self.failed_nodes.discard(node_id)
        self._recalculate_all_link_shares()

    def register_cluster_observer(self, callback: Callable[[str, List[str]], None]) -> None:
        if callback not in self._cluster_observers:
            self._cluster_observers.append(callback)
    
    def initiate_file_transfer(
        self,
        source_node_id: str,
        target_node_id: str,
        file_name: str,
        file_size: int,
        *,
        backing_file_id: Optional[str] = None,
        segment_offset: int = 0,
    ) -> Optional[FileTransfer]:
        """Initiate a file transfer between nodes"""
        if source_node_id not in self.nodes or target_node_id not in self.nodes:
            return None

        effective_target_id = self._ensure_target_capacity(target_node_id, file_size)
        if not effective_target_id:
            return None

        route = self._compute_route(source_node_id, effective_target_id)
        if not route or route[-1] != effective_target_id:
            return None

        target_node = self.nodes[effective_target_id]
        self._maybe_expand_cluster(effective_target_id)
            
        file_id = hashlib.md5(f"{file_name}-{self.simulator.now}".encode()).hexdigest()
        chunk_size = self._recommend_chunk_size(file_size, route)

        transfer = target_node.initiate_file_transfer(
            file_id,
            file_name,
            file_size,
            current_time=self.simulator.now,
            source_node=source_node_id,
            preferred_chunk_size=chunk_size,
            backing_file_id=backing_file_id,
            segment_offset=segment_offset,
        )

        if not transfer and self.scaling.enabled:
            retry_target_id = self._spawn_replica_node(effective_target_id) or effective_target_id
            next_target_id = self._select_storage_node(retry_target_id, file_size)
            if next_target_id and next_target_id in self.nodes:
                target_node = self.nodes[next_target_id]
                route = self._compute_route(source_node_id, next_target_id)
                if not route or route[-1] != next_target_id:
                    return None
                chunk_size = self._recommend_chunk_size(file_size, route)
                transfer = target_node.initiate_file_transfer(
                    file_id,
                    file_name,
                    file_size,
                    current_time=self.simulator.now,
                    source_node=source_node_id,
                    preferred_chunk_size=chunk_size,
                    backing_file_id=backing_file_id,
                    segment_offset=segment_offset,
                )
                effective_target_id = next_target_id

        if transfer:
            self._register_file_aliases(transfer)
            self.transfer_operations[source_node_id][file_id] = transfer
            self._schedule_next_chunk(source_node_id, effective_target_id, file_id, route)
            return transfer
        return None

    def initiate_replica_transfer(
        self,
        owner_node_id: str,
        target_node_id: str,
        file_id: str,
    ) -> Optional[FileTransfer]:
        if owner_node_id not in self.nodes or target_node_id not in self.nodes:
            return None

        route = self._compute_route(owner_node_id, target_node_id)
        if not route or len(route) < 2:
            return None

        source_node = self.nodes[owner_node_id]
        retrieval = source_node.retrieve_file(file_id, target_node_id)
        if not retrieval:
            return None

        target_node = self.nodes[target_node_id]
        transfer = target_node.initiate_file_transfer(
            retrieval.file_id,
            retrieval.file_name,
            retrieval.total_size,
            current_time=self.simulator.now,
            source_node=owner_node_id,
        )
        if not transfer:
            return None

        transfer.chunks = retrieval.chunks
        transfer.is_retrieval = True
        transfer.backing_file_id = file_id
        transfer.created_at = self.simulator.now
        self._register_file_aliases(transfer)

        self.transfer_operations[owner_node_id][transfer.file_id] = transfer
        self._schedule_next_chunk(owner_node_id, target_node_id, transfer.file_id, route)
        return transfer
    
    def get_network_stats(self) -> Dict[str, float]:
        """Get overall network statistics"""
        total_bandwidth = sum(n.bandwidth for n in self.nodes.values())
        used_bandwidth = sum(n.network_utilization for n in self.nodes.values())
        total_storage = sum(n.total_storage for n in self.nodes.values())
        used_storage = sum(n.used_storage for n in self.nodes.values())
        
        bandwidth_utilization = ((used_bandwidth / total_bandwidth) * 100) if total_bandwidth else 0.0
        storage_utilization = ((used_storage / total_storage) * 100) if total_storage else 0.0

        return {
            "total_nodes": len(self.nodes),
            "total_bandwidth_bps": total_bandwidth,
            "used_bandwidth_bps": used_bandwidth,
            "bandwidth_utilization": bandwidth_utilization,
            "total_storage_bytes": total_storage,
            "used_storage_bytes": used_storage,
            "storage_utilization": storage_utilization,
            "active_transfers": sum(len(t) for t in self.transfer_operations.values())
        }

    def register_observer(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback to receive transfer events."""
        self.transfer_observers.append(callback)

    def get_cluster_nodes(self, node_id: str) -> Set[str]:
        """Return the cluster (root + replicas) for a given node id."""
        return set(self._get_cluster_nodes(node_id))

    def get_node_telemetry(self, node_id: str) -> Optional[NodeTelemetry]:
        return self.node_telemetry.get(node_id)

    def get_last_scaling_trigger(self, node_id: str) -> Optional[str]:
        return self._last_scaling_trigger.get(node_id)

    def get_replica_parent(self, replica_id: str) -> Optional[str]:
        return self._replica_parents.get(replica_id)

    def get_replica_children(self, parent_id: str) -> List[str]:
        return self._get_replica_children(parent_id)

    def locate_file(self, file_name: str) -> List[Tuple[str, FileTransfer]]:
        matches: List[Tuple[str, FileTransfer]] = []
        if not file_name:
            return matches

        manifest = self.file_manifests.get(file_name)
        if manifest:
            for segment in manifest.segments:
                node = self.nodes.get(segment.node_id)
                if not node or segment.node_id in self.failed_nodes:
                    continue
                transfer = node.stored_files.get(segment.file_id)
                if transfer:
                    matches.append((segment.node_id, transfer))
            if matches:
                return matches

        normalized = file_name.lower()
        for node_id, node in self.nodes.items():
            if node_id in self.failed_nodes:
                continue
            for transfer in node.stored_files.values():
                backing = transfer.backing_file_id
                if (
                    normalized == transfer.file_name.lower()
                    or file_name == transfer.file_id
                    or (backing and file_name == backing)
                ):
                    matches.append((node_id, transfer))

        def _priority(entry: Tuple[str, FileTransfer]) -> Tuple[int, float]:
            node_id, stored_transfer = entry
            root = self._get_root_id(node_id)
            is_root = 0 if node_id == root else 1
            completed = stored_transfer.completed_at or float("inf")
            return (is_root, completed)

        matches.sort(key=_priority)
        return matches

    def get_route(self, source_node_id: str, target_node_id: str) -> Optional[List[str]]:
        """Expose the currently computed routing path for testing/inspection."""
        return self._compute_route(source_node_id, target_node_id)

    def _register_file_aliases(self, transfer: FileTransfer) -> None:
        self.file_names[transfer.file_id] = transfer.file_name
        backing_id = transfer.backing_file_id
        if backing_id:
            self.file_names[backing_id] = transfer.file_name

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        if "file_name" not in payload:
            lookup_keys = [
                payload.get("file_id"),
                payload.get("dataset_id"),
                payload.get("backing_file_id"),
            ]
            for key in lookup_keys:
                if key and key in self.file_names:
                    payload["file_name"] = self.file_names[key]
                    break
        event = {"type": event_type, "time": self.simulator.now, **payload}
        for observer in self.transfer_observers:
            observer(event)

    def _allocate_ip(self) -> str:
        octet = 2 + (self._ip_counter % 250)
        subnet = self._ip_counter // 250
        self._ip_counter += 1
        return f"10.0.{subnet}.{octet}"

    def _link_key(self, source_node_id: str, target_node_id: str) -> Tuple[str, str]:
        return (source_node_id, target_node_id)

    def _chunk_key(self, source_node_id: str, target_node_id: str, file_id: str, chunk_id: int) -> ChunkKey:
        return (source_node_id, target_node_id, file_id, chunk_id)

    def _link_capacity(self, source_node_id: str, target_node_id: str) -> float:
        if self._should_skip_node(source_node_id) or self._should_skip_node(target_node_id):
            return 0.0
        if self._is_link_failed(source_node_id, target_node_id):
            return 0.0
        source_node = self.nodes[source_node_id]
        target_node = self.nodes[target_node_id]
        link_bandwidth = min(
            source_node.connections.get(target_node_id, 0),
            target_node.connections.get(source_node_id, 0),
        )
        return float(min(link_bandwidth, source_node.bandwidth, target_node.bandwidth))

    def _establish_link(self, node1_id: str, node2_id: str, bandwidth_mbps: int, latency_ms: float) -> None:
        self.nodes[node1_id].add_connection(node2_id, bandwidth_mbps, latency_ms)
        self.nodes[node2_id].add_connection(node1_id, bandwidth_mbps, latency_ms)
        self.link_latency_ms[(node1_id, node2_id)] = latency_ms
        self.link_latency_ms[(node2_id, node1_id)] = latency_ms

    def _mirror_replica_links(self, node_a: str, node_b: str, bandwidth_mbps: int, latency_ms: float) -> None:
        for replica_id in self._get_cluster_nodes(node_a):
            if replica_id == node_a or replica_id not in self.nodes:
                continue
            self._establish_link(replica_id, node_b, bandwidth_mbps, latency_ms)
        for replica_id in self._get_cluster_nodes(node_b):
            if replica_id == node_b or replica_id not in self.nodes:
                continue
            self._establish_link(node_a, replica_id, bandwidth_mbps, latency_ms)

    def _neighbor_links(self, node_id: str) -> List[Tuple[str, float]]:
        if self._should_skip_node(node_id):
            return []
        node = self.nodes[node_id]
        neighbors: List[Tuple[str, float]] = []
        for neighbor_id in node.connections.keys():
            if neighbor_id not in self.nodes:
                continue
            if self._should_skip_node(neighbor_id):
                continue
            if self._is_link_failed(node_id, neighbor_id):
                continue
            latency = self.link_latency_ms.get((node_id, neighbor_id), 1.0)
            neighbors.append((neighbor_id, latency))
        return neighbors

    def _compute_route(self, source_node_id: str, target_node_id: str) -> Optional[List[str]]:
        if self._should_skip_node(source_node_id) or self._should_skip_node(target_node_id):
            return None
        if source_node_id == target_node_id:
            return [source_node_id]
        if self.routing_strategy == "distance_vector":
            return self._compute_route_distance_vector(source_node_id, target_node_id)
        return self._compute_route_link_state(source_node_id, target_node_id)

    def _compute_route_link_state(self, source_node_id: str, target_node_id: str) -> Optional[List[str]]:
        if source_node_id not in self.nodes or target_node_id not in self.nodes:
            return None
        visited: Set[str] = set()
        heap: List[Tuple[float, str, Optional[str]]] = [(0.0, source_node_id, None)]
        parents: Dict[str, Optional[str]] = {source_node_id: None}

        while heap:
            cost, node_id, parent = heapq.heappop(heap)
            if node_id in visited or self._should_skip_node(node_id):
                continue
            visited.add(node_id)
            parents[node_id] = parent
            if node_id == target_node_id:
                break
            for neighbor_id, latency in self._neighbor_links(node_id):
                if neighbor_id in visited:
                    continue
                heapq.heappush(heap, (cost + latency, neighbor_id, node_id))

        if target_node_id not in parents:
            return None
        return self._build_path(parents, source_node_id, target_node_id)

    def _recommend_chunk_size(self, file_size: int, route: Optional[List[str]]) -> int:
        if file_size <= 0:
            return StorageVirtualNode._MIN_CHUNK_SIZE_BYTES
        if not route or len(route) < 2:
            upper = min(StorageVirtualNode._MAX_CHUNK_SIZE_BYTES, file_size)
            return max(StorageVirtualNode._MIN_CHUNK_SIZE_BYTES, upper)

        hop_count = max(1, len(route) - 1)
        capacities: List[float] = []
        for idx in range(len(route) - 1):
            capacity = self._link_capacity(route[idx], route[idx + 1])
            if capacity > 0:
                capacities.append(capacity)
        if capacities:
            bottleneck_bps = min(capacities)
        else:
            bottleneck_bps = 500_000_000  # ~500 Mbps fallback

        bytes_per_second = max(64 * 1024, bottleneck_bps / 8)
        target_duration = 0.35 + 0.15 * math.log2(hop_count + 1)
        chunk_size = int(bytes_per_second * target_duration)
        chunk_size = max(StorageVirtualNode._MIN_CHUNK_SIZE_BYTES, chunk_size)
        chunk_size = min(chunk_size, StorageVirtualNode._MAX_CHUNK_SIZE_BYTES)
        return max(1, min(chunk_size, file_size))

    def _compute_route_distance_vector(self, source_node_id: str, target_node_id: str) -> Optional[List[str]]:
        active_nodes = [node_id for node_id in self.nodes if not self._should_skip_node(node_id)]
        if source_node_id not in active_nodes or target_node_id not in active_nodes:
            return None

        dist: Dict[str, float] = {node_id: float("inf") for node_id in active_nodes}
        parents: Dict[str, Optional[str]] = {source_node_id: None}
        dist[source_node_id] = 0.0

        for _ in range(len(active_nodes) - 1):
            updated = False
            for node_id in active_nodes:
                if self._should_skip_node(node_id):
                    continue
                if dist[node_id] == float("inf"):
                    continue
                for neighbor_id, latency in self._neighbor_links(node_id):
                    new_cost = dist[node_id] + latency
                    if new_cost < dist.get(neighbor_id, float("inf")):
                        dist[neighbor_id] = new_cost
                        parents[neighbor_id] = node_id
                        updated = True
            if not updated:
                break

        if dist.get(target_node_id, float("inf")) == float("inf"):
            return None
        return self._build_path(parents, source_node_id, target_node_id)

    def _build_path(
        self,
        parents: Dict[str, Optional[str]],
        source_node_id: str,
        target_node_id: str,
    ) -> Optional[List[str]]:
        path: List[str] = []
        current: Optional[str] = target_node_id
        while current is not None:
            path.append(current)
            if current == source_node_id:
                path.reverse()
                return path
            current = parents.get(current)
        return None

    def _should_skip_node(self, node_id: str) -> bool:
        return node_id in self.failed_nodes

    def _is_link_failed(self, node1_id: str, node2_id: str) -> bool:
        return self._link_key(node1_id, node2_id) in self.failed_links

    def _ensure_tick(self) -> None:
        if not self._tick_scheduled:
            self._tick_scheduled = True
            self.simulator.schedule_in(0.0, self._network_tick)

    def _attach_chunk_to_link(self, chunk_key: ChunkKey, state: ActiveChunk) -> bool:
        source, target = state.current_hop_nodes()
        if self._link_capacity(source, target) <= 0:
            return False
        if not self._start_chunk_hop(state):
            return False
        link_key = self._link_key(source, target)
        self.link_active_chunks[link_key].add(chunk_key)
        self.node_active_chunks[source].add(chunk_key)
        self.node_active_chunks[target].add(chunk_key)
        self._recalculate_link_share(source, target)
        return True

    def _detach_chunk_from_link(self, chunk_key: ChunkKey, state: ActiveChunk) -> None:
        source, target = state.current_hop_nodes()
        self._finish_chunk_hop(state)
        link_key = self._link_key(source, target)
        link_chunks = self.link_active_chunks.get(link_key)
        if link_chunks:
            link_chunks.discard(chunk_key)
            if not link_chunks:
                self.link_active_chunks.pop(link_key, None)
        for node_id in (source, target):
            node_chunks = self.node_active_chunks.get(node_id)
            if node_chunks:
                node_chunks.discard(chunk_key)
                if not node_chunks:
                    self.node_active_chunks.pop(node_id, None)
            self._update_node_bandwidth(node_id)
        self._recalculate_link_share(source, target)

    def _advance_chunk_to_next_hop(self, chunk_key: ChunkKey, state: ActiveChunk) -> None:
        self._detach_chunk_from_link(chunk_key, state)
        state.hop_index += 1
        if not self._attach_chunk_to_link(chunk_key, state):
            self._fail_active_chunk(chunk_key, "Insufficient node resources for next hop")

    def _register_node_cluster(self, node_id: str, root_id: Optional[str] = None) -> None:
        root = root_id or self.node_roots.get(node_id) or node_id
        self.node_roots[node_id] = root
        cluster = self.cluster_nodes.setdefault(root, set())
        cluster.add(node_id)
        self._notify_cluster_observers(root)

    def _get_root_id(self, node_id: str) -> str:
        return self.node_roots.get(node_id, node_id)

    def _get_cluster_nodes(self, node_id: str) -> Set[str]:
        root = self._get_root_id(node_id)
        if root not in self.cluster_nodes and root in self.nodes:
            self.cluster_nodes[root] = {root}
            self.node_roots[root] = root
            self._notify_cluster_observers(root)
        return self.cluster_nodes.get(root, set())

    def _notify_cluster_observers(self, root_id: str) -> None:
        cluster = sorted(self.cluster_nodes.get(root_id, set()))
        for callback in self._cluster_observers:
            try:
                callback(root_id, cluster)
            except Exception:
                continue

    def _get_replica_children(self, parent_id: str) -> List[str]:
        return [replica_id for replica_id, recorded_parent in self._replica_parents.items() if recorded_parent == parent_id]

    def _update_replica_triggers(self, parent_id: str, trigger: str) -> None:
        for replica_id in self._get_replica_children(parent_id):
            self._last_scaling_trigger[replica_id] = trigger

    def _node_projected_usage(self, node: StorageVirtualNode) -> float:
        if hasattr(node, "projected_storage_usage"):
            return node.projected_storage_usage  # type: ignore[attr-defined]
        return node.used_storage + sum(t.total_size for t in node.active_transfers.values())

    def _node_has_capacity(self, node: StorageVirtualNode, required_size: Optional[int]) -> bool:
        if required_size is None:
            return True
        projected = self._node_projected_usage(node)
        return (projected + required_size) <= node.total_storage

    def _node_free_bytes(self, node_id: str) -> int:
        node = self.nodes.get(node_id)
        if not node:
            return 0
        return getattr(node, "free_storage", 0)

    def _projected_usage_ratio(self, node: StorageVirtualNode) -> float:
        projected = self._node_projected_usage(node)
        return (projected / node.total_storage) if node.total_storage else 0.0

    def _select_storage_node(self, requested_node_id: str, required_size: Optional[int] = None) -> Optional[str]:
        candidates = [
            self.nodes[node_id]
            for node_id in self._get_cluster_nodes(requested_node_id)
            if node_id in self.nodes and node_id not in self.failed_nodes
        ]
        if not candidates:
            return None

        eligible = [node for node in candidates if self._node_has_capacity(node, required_size)]
        if not eligible:
            return None

        eligible.sort(key=self._projected_usage_ratio)
        return eligible[0].node_id

    def _reachable_nodes(self, source_node_id: str) -> Set[str]:
        if source_node_id not in self.nodes or source_node_id in self.failed_nodes:
            return set()
        visited: Set[str] = set()
        queue: deque[str] = deque([source_node_id])
        while queue:
            node_id = queue.popleft()
            if node_id in visited or node_id in self.failed_nodes:
                continue
            visited.add(node_id)
            node = self.nodes.get(node_id)
            if not node:
                continue
            for neighbor_id in node.connections.keys():
                if neighbor_id in visited or neighbor_id in self.failed_nodes:
                    continue
                if self._is_link_failed(node_id, neighbor_id):
                    continue
                queue.append(neighbor_id)
        return visited

    def _select_ingest_target(self, source_node_id: str, file_size: int, exclude: Optional[Set[str]] = None) -> Optional[str]:
        reachable = self._reachable_nodes(source_node_id)
        if not reachable:
            reachable = {source_node_id}
        cluster = self._get_cluster_nodes(source_node_id)
        ranked: List[Tuple[int, float, StorageVirtualNode]] = []
        for node_id in reachable:
            node = self.nodes.get(node_id)
            if not node or node_id in self.failed_nodes:
                continue
            if exclude and node_id in exclude:
                continue
            if not self._node_has_capacity(node, file_size):
                continue
            if node_id == source_node_id:
                cluster_priority = 2
            elif node_id in cluster:
                cluster_priority = 0
            else:
                cluster_priority = 1
            ranked.append((cluster_priority, self._projected_usage_ratio(node), node))

        if ranked:
            ranked.sort(key=lambda entry: (entry[0], entry[1]))
            return ranked[0][2].node_id

        return self._ensure_target_capacity(source_node_id, file_size)

    def store_local_file(self, node_id: str, file_name: str, file_size: int) -> Optional[FileTransfer]:
        node = self.nodes.get(node_id)
        if not node or node_id in self.failed_nodes:
            return None
        transfer = node.store_local_file(file_name, file_size, current_time=self.simulator.now)
        if not transfer:
            return None
        self._register_file_aliases(transfer)
        self._finalize_transfer(node_id, node_id, transfer.file_id, transfer)
        return transfer

    def get_file_manifest(self, file_name: str) -> Optional[FileManifest]:
        return self.file_manifests.get(file_name)

    def assemble_file(self, file_name: str, target_node_id: str) -> Optional[FileTransfer]:
        manifest = self.file_manifests.get(file_name)
        if not manifest or target_node_id not in self.nodes:
            return None
        last_transfer: Optional[FileTransfer] = None
        for segment in manifest.segments:
            if segment.node_id == target_node_id:
                continue
            replica_transfer = self.initiate_replica_transfer(segment.node_id, target_node_id, segment.file_id)
            if not replica_transfer:
                return None
            last_transfer = replica_transfer
            segment.node_id = target_node_id
            segment.file_id = replica_transfer.file_id
        if last_transfer:
            return last_transfer
        transfer = FileTransfer(
            file_id=manifest.master_id,
            file_name=manifest.file_name,
            total_size=manifest.total_size,
            chunks=[],
            status=TransferStatus.COMPLETED,
            created_at=self.simulator.now,
            completed_at=self.simulator.now,
            is_retrieval=True,
            backing_file_id=manifest.master_id,
            target_node=target_node_id,
        )
        self._register_file_aliases(transfer)
        return transfer

    def ingest_file(
        self,
        source_node_id: str,
        file_name: str,
        file_size: int,
        *,
        prefer_local: bool = False,
    ) -> Optional[Tuple[str, FileTransfer]]:
        if source_node_id not in self.nodes or source_node_id in self.failed_nodes:
            return None
        if prefer_local:
            transfer = self.store_local_file(source_node_id, file_name, file_size)
            if not transfer:
                return None
            manifest = FileManifest(
                file_name=file_name,
                master_id=transfer.backing_file_id or transfer.file_id,
                total_size=file_size,
                segments=[
                    FileSegment(
                        node_id=source_node_id,
                        file_id=transfer.file_id,
                        size=file_size,
                        offset=0,
                    )
                ],
                created_at=self.simulator.now,
            )
            self._register_manifest(manifest)
            return source_node_id, transfer

        manifest, last_transfer = self._distribute_file_segments(source_node_id, file_name, file_size)
        if not manifest or not last_transfer:
            return None
        self._register_manifest(manifest)
        last_segment = manifest.segments[-1]
        return last_segment.node_id, last_transfer

    def _collect_node_telemetry(self, node: StorageVirtualNode) -> NodeTelemetry:
        projected = node.projected_storage_usage if hasattr(node, "projected_storage_usage") else node.used_storage
        storage_ratio = (projected / node.total_storage) if node.total_storage else 0.0
        bandwidth_ratio = (node.network_utilization / node.bandwidth) if node.bandwidth else 0.0
        os_memory_ratio = (
            node.virtual_os.used_memory / node.memory_capacity_bytes
            if getattr(node, "memory_capacity_bytes", 0) else 0.0
        )
        baseline = self._os_failure_baseline.get(node.node_id, 0)
        os_failure_delta = node.os_process_failures - baseline
        reserved_bytes = 0
        if hasattr(node, "disk") and hasattr(node.disk, "reserved_bytes"):
            reserved_bytes = node.disk.reserved_bytes
        telemetry = NodeTelemetry(
            node_id=node.node_id,
            storage_ratio=storage_ratio,
            bandwidth_ratio=bandwidth_ratio,
            os_memory_ratio=os_memory_ratio,
            os_failure_delta=os_failure_delta,
            used_bytes=node.used_storage,
            reserved_bytes=reserved_bytes,
            timestamp=self.simulator.now,
        )
        self.node_telemetry[node.node_id] = telemetry
        return telemetry

    def _register_manifest(self, manifest: FileManifest) -> None:
        existing = self.file_manifests.get(manifest.file_name)
        if existing:
            if self.file_manifests_by_id.get(existing.master_id) is existing:
                self.file_manifests_by_id.pop(existing.master_id, None)
            if self.file_names.get(existing.master_id) == existing.file_name:
                self.file_names.pop(existing.master_id, None)
            for segment in existing.segments:
                if self.segment_manifests.get(segment.file_id) is existing:
                    self.segment_manifests.pop(segment.file_id, None)
                if self.file_names.get(segment.file_id) == existing.file_name:
                    self.file_names.pop(segment.file_id, None)
        self.file_manifests[manifest.file_name] = manifest
        self.file_manifests_by_id[manifest.master_id] = manifest
        self.file_names[manifest.master_id] = manifest.file_name
        for segment in manifest.segments:
            self.segment_manifests[segment.file_id] = manifest
            self.file_names[segment.file_id] = manifest.file_name

    def _distribute_file_segments(
        self,
        source_node_id: str,
        file_name: str,
        file_size: int,
    ) -> Tuple[Optional[FileManifest], Optional[FileTransfer]]:
        master_id = hashlib.md5(f"{file_name}-{self.simulator.now}-{source_node_id}".encode()).hexdigest()
        bytes_remaining = file_size
        offset = 0
        segments: List[FileSegment] = []
        exhausted: Set[str] = set()
        last_transfer: Optional[FileTransfer] = None

        while bytes_remaining > 0:
            target_id = self._select_ingest_target(source_node_id, max(bytes_remaining, 1), exclude=exhausted)
            if not target_id:
                break
            available = self._node_free_bytes(target_id)
            if available <= 0:
                exhausted.add(target_id)
                continue
            segment_size = min(bytes_remaining, available)
            transfer = self.initiate_file_transfer(
                source_node_id,
                target_id,
                file_name,
                segment_size,
                backing_file_id=master_id,
                segment_offset=offset,
            )
            if not transfer:
                exhausted.add(target_id)
                continue
            actual_target = transfer.target_node or target_id
            segments.append(
                FileSegment(
                    node_id=actual_target,
                    file_id=transfer.file_id,
                    size=segment_size,
                    offset=offset,
                )
            )
            last_transfer = transfer
            bytes_remaining -= segment_size
            offset += segment_size

        if bytes_remaining > 0 or not segments or not last_transfer:
            return None, None

        manifest = FileManifest(
            file_name=file_name,
            master_id=master_id,
            total_size=file_size,
            segments=segments,
            created_at=self.simulator.now,
        )
        return manifest, last_transfer

    def _cause_ratio(self, telemetry: NodeTelemetry, cause: str) -> float:
        if cause == "storage":
            return telemetry.storage_ratio
        if cause == "bandwidth":
            return telemetry.bandwidth_ratio
        if cause == "os_memory":
            return telemetry.os_memory_ratio
        if cause == "os_failures":
            return float(max(0, telemetry.os_failure_delta))
        return 0.0

    def _node_overload_cause(self, node: StorageVirtualNode) -> Optional[Tuple[str, NodeTelemetry]]:
        if not self.scaling.enabled:
            return None
        telemetry = self._collect_node_telemetry(node)
        breaches: Dict[str, NodeTelemetry] = {}

        if telemetry.storage_ratio >= self.scaling.storage_utilization_threshold:
            breaches["storage"] = telemetry
        if telemetry.bandwidth_ratio >= self.scaling.bandwidth_utilization_threshold:
            breaches["bandwidth"] = telemetry
        if (
            self.scaling.os_memory_utilization_threshold is not None
            and telemetry.os_memory_ratio >= self.scaling.os_memory_utilization_threshold
        ):
            breaches["os_memory"] = telemetry

        if self.scaling.os_failure_threshold is not None:
            baseline = self._os_failure_baseline.get(node.node_id, 0)
            if telemetry.os_failure_delta >= self.scaling.os_failure_threshold:
                self._os_failure_baseline[node.node_id] = node.os_process_failures
                breaches["os_failures"] = telemetry

        if not breaches:
            return None

        priority = self.scaling.trigger_priority or ["storage", "bandwidth", "os_memory", "os_failures"]
        for cause in priority:
            if cause in breaches:
                return cause, breaches[cause]
        cause, telem = next(iter(breaches.items()))
        return cause, telem

    def _ensure_target_capacity(self, requested_node_id: str, file_size: int) -> Optional[str]:
        target_id = self._select_storage_node(requested_node_id, file_size)
        if target_id:
            return target_id

        if not self.scaling.enabled:
            return None

        reference_candidates = list(self._get_cluster_nodes(requested_node_id)) or [requested_node_id]
        for candidate_id in reference_candidates:
            if candidate_id in self.nodes:
                self._spawn_replica_node(candidate_id)
                break

        return self._select_storage_node(requested_node_id, file_size)

    def _spawn_replica_node(self, reference_node_id: str, force: bool = False) -> Optional[str]:
        if reference_node_id not in self.nodes:
            return None
        if not self.scaling.enabled and not force:
            return None

        root_id = self._get_root_id(reference_node_id)
        cluster = self._get_cluster_nodes(root_id)
        if len(cluster) - 1 >= self.scaling.max_replicas_per_root:
            return None

        reference_node = self.nodes[reference_node_id]
        replica_suffix = len(cluster)
        replica_id = f"{root_id}-replica-{replica_suffix}"
        while replica_id in self.nodes:
            replica_suffix += 1
            replica_id = f"{root_id}-replica-{replica_suffix}"

        replica = reference_node.clone(
            replica_id,
            storage_factor=self.scaling.replica_storage_factor,
            bandwidth_factor=self.scaling.replica_bandwidth_factor,
        )
        self.add_node(replica, root_id=root_id)
        self._replica_parents[replica_id] = reference_node_id

        for neighbor_id, bandwidth_bps in list(reference_node.connections.items()):
            if neighbor_id not in self.nodes:
                continue
            bandwidth_mbps = max(1, int(bandwidth_bps / 1000000))
            latency = reference_node.get_link_latency(neighbor_id)
            self.connect_nodes(replica_id, neighbor_id, bandwidth=bandwidth_mbps, latency_ms=latency)

        parent_link_bandwidth = max(1, int(reference_node.bandwidth / 1000000))
        self.connect_nodes(replica_id, reference_node.node_id, bandwidth=parent_link_bandwidth, latency_ms=1.0)
        self._schedule_replica_seed(reference_node_id, replica_id)
        self._notify_cluster_observers(root_id)
        return replica_id

    def _ensure_replica_coverage(self, node_id: str) -> None:
        if not self.scaling.auto_replication_enabled:
            return
        desired = max(0, min(self.scaling.min_replicas_per_root, self.scaling.max_replicas_per_root))
        if desired == 0:
            return
        root_id = self._get_root_id(node_id)
        cluster = self._get_cluster_nodes(root_id)
        healthy = [
            member_id
            for member_id in cluster
            if member_id != root_id and member_id in self.nodes and member_id not in self.failed_nodes
        ]
        missing = desired - len(healthy)
        if missing <= 0:
            return

        while missing > 0:
            reference_candidates = [root_id, *healthy]
            reference_id = next(
                (candidate for candidate in reference_candidates if candidate in self.nodes and candidate not in self.failed_nodes),
                None,
            )
            if not reference_id:
                break
            replica_id = self._spawn_replica_node(reference_id, force=True)
            if not replica_id:
                break
            healthy.append(replica_id)
            missing -= 1

    def _schedule_replica_seed(self, source_node_id: str, replica_id: str, attempt: int = 0) -> None:
        if self.scaling.replica_seed_limit == 0:
            return
        source_node = self.nodes.get(source_node_id)
        replica_node = self.nodes.get(replica_id)
        if not source_node or not replica_node:
            return
        stored_files = list(source_node.stored_files.values())
        if not stored_files:
            if attempt < 5:
                self.simulator.schedule_in(
                    0.05,
                    self._schedule_replica_seed,
                    source_node_id,
                    replica_id,
                    attempt + 1,
                )
            return
        replica_backing_ids = {
            transfer.backing_file_id or transfer.file_id
            for transfer in replica_node.stored_files.values()
        }
        seed_limit = self.scaling.replica_seed_limit or len(stored_files)
        seeded = 0
        for transfer in stored_files:
            if seeded >= seed_limit:
                break
            backing_id = transfer.backing_file_id or transfer.file_id
            if backing_id in replica_backing_ids:
                continue
            route = self._compute_route(source_node_id, replica_id)
            if not route or len(route) < 2:
                continue
            replica_transfer = self.initiate_replica_transfer(source_node_id, replica_id, transfer.file_id)
            if replica_transfer:
                seeded += 1
                replica_backing_ids.add(backing_id)

    def _node_has_dataset(self, node_id: str, dataset_id: str) -> bool:
        node = self.nodes.get(node_id)
        if not node:
            return False

        def matches(transfers: Dict[str, FileTransfer]) -> bool:
            for transfer in transfers.values():
                backing = transfer.backing_file_id or transfer.file_id
                if backing == dataset_id:
                    return True
            return False

        return matches(node.stored_files) or matches(node.active_transfers)

    def _replicate_across_cluster(self, owner_node_id: str, transfer: FileTransfer) -> None:
        if transfer.is_retrieval:
            return
        owner_node = self.nodes.get(owner_node_id)
        if not owner_node or owner_node_id in self.failed_nodes:
            return

        dataset_id = transfer.backing_file_id or transfer.file_id
        cluster_nodes = [
            node_id
            for node_id in self._get_cluster_nodes(owner_node_id)
            if node_id in self.nodes and node_id not in self.failed_nodes
        ]

        root_id = self._get_root_id(owner_node_id)
        if root_id in self.nodes and root_id not in self.failed_nodes and root_id not in cluster_nodes:
            cluster_nodes.append(root_id)
        if root_id in cluster_nodes and root_id != owner_node_id:
            cluster_nodes.remove(root_id)
            cluster_nodes.insert(0, root_id)

        for node_id in cluster_nodes:
            if node_id == owner_node_id:
                continue
            if self._node_has_dataset(node_id, dataset_id):
                continue
            replica_transfer = self.initiate_replica_transfer(owner_node_id, node_id, transfer.file_id)
            if not replica_transfer:
                self._emit_event(
                    "replica_sync_failed",
                    file_id=transfer.file_id,
                    dataset_id=dataset_id,
                    source=owner_node_id,
                    target=node_id,
                )

    def _is_node_overloaded(self, node: StorageVirtualNode) -> Tuple[bool, Optional[str], Optional[NodeTelemetry]]:
        cause = self._node_overload_cause(node)
        if cause is None:
            return False, None, None
        trigger, telemetry = cause
        return True, trigger, telemetry

    def _maybe_expand_cluster(self, node_id: str) -> None:
        if not self.scaling.enabled or node_id not in self.nodes:
            return
        root_id = self._get_root_id(node_id)
        cluster = self._get_cluster_nodes(root_id)
        at_capacity = (len(cluster) - 1) >= self.scaling.max_replicas_per_root
        overloaded: List[Tuple[StorageVirtualNode, Optional[str], Optional[NodeTelemetry]]] = []
        for nid in cluster:
            node = self.nodes.get(nid)
            if not node:
                continue
            overloaded_flag, trigger, telemetry = self._is_node_overloaded(node)
            if overloaded_flag:
                overloaded.append((node, trigger, telemetry))
        if not overloaded:
            return
        trigger_priority = self.scaling.trigger_priority or ["storage", "bandwidth", "os_memory", "os_failures"]

        def priority_index(trigger: Optional[str]) -> int:
            if trigger is None:
                return len(trigger_priority) + 1
            try:
                return trigger_priority.index(trigger)
            except ValueError:
                return len(trigger_priority)

        overloaded.sort(
            key=lambda entry: (
                priority_index(entry[1]),
                -self._cause_ratio(entry[2], entry[1] or ""),
                -entry[0].network_utilization,
            ),
        )
        winner, trigger, telemetry = overloaded[0]
        if at_capacity:
            if trigger:
                self._update_replica_triggers(winner.node_id, trigger)
            return
        replica_id = self._spawn_replica_node(winner.node_id)
        if replica_id:
            self._last_scaling_trigger[replica_id] = trigger or "unknown"

    def _schedule_next_chunk(
        self,
        source_node_id: str,
        target_node_id: str,
        file_id: str,
        route: Optional[List[str]] = None,
    ) -> None:
        transfer = self.transfer_operations[source_node_id].get(file_id)
        if not transfer:
            return

        next_chunk = next((c for c in transfer.chunks if c.status != TransferStatus.COMPLETED), None)
        if not next_chunk:
            self._finalize_transfer(source_node_id, target_node_id, file_id, transfer)
            return

        path = route or self._compute_route(source_node_id, target_node_id)
        if not path or len(path) < 2:
            transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=file_id,
                source=source_node_id,
                target=target_node_id,
                reason="No available route",
            )
            self.transfer_operations[source_node_id].pop(file_id, None)
            target_node = self.nodes.get(target_node_id)
            if target_node:
                target_node.abort_transfer(file_id)
            return

        first_hop_source, first_hop_target = path[0], path[1]
        if self._link_capacity(first_hop_source, first_hop_target) <= 0:
            transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=file_id,
                source=source_node_id,
                target=target_node_id,
                reason="No available bandwidth",
            )
            self.transfer_operations[source_node_id].pop(file_id, None)
            target_node = self.nodes.get(target_node_id)
            if target_node:
                target_node.abort_transfer(file_id)
            return

        chunk_key = self._chunk_key(source_node_id, target_node_id, file_id, next_chunk.chunk_id)
        if chunk_key in self.active_chunks:
            return

        state = ActiveChunk(
            source=source_node_id,
            target=target_node_id,
            transfer=transfer,
            chunk=next_chunk,
            remaining_bytes=float(next_chunk.size),
            path=path,
        )

        self.active_chunks[chunk_key] = state
        self.chunk_bandwidths[chunk_key] = 0.0
        if not self._attach_chunk_to_link(chunk_key, state):
            self._fail_active_chunk(chunk_key, "Insufficient node resources for chunk transmission")
            return

        next_chunk.status = TransferStatus.IN_PROGRESS
        transfer.status = TransferStatus.IN_PROGRESS

        self._ensure_tick()

    def _network_tick(self) -> None:
        if not self.active_chunks:
            self._tick_scheduled = False
            return

        self._recalculate_all_link_shares()

        completed: List[Tuple[ChunkKey, bool, Optional[str]]] = []
        for chunk_key, state in list(self.active_chunks.items()):
            share = self.chunk_bandwidths.get(chunk_key, 0.0)
            if share <= 0:
                continue

            bytes_transferred = share * self.tick_interval / 8
            state.remaining_bytes -= bytes_transferred
            while state.remaining_bytes <= 0 and not state.on_last_hop():
                overflow = -state.remaining_bytes
                self._advance_chunk_to_next_hop(chunk_key, state)
                state.remaining_bytes = float(state.chunk.size) - overflow
            if state.remaining_bytes <= 0 and state.on_last_hop():
                completed.append((chunk_key, True, None))

        for chunk_key, success, reason in completed:
            if success:
                self._complete_active_chunk(chunk_key, self.chunk_bandwidths.get(chunk_key, 0.0))
            else:
                self._fail_active_chunk(chunk_key, reason)

        if self.active_chunks:
            self.simulator.schedule_in(self.tick_interval, self._network_tick)
        else:
            self._tick_scheduled = False

    def _complete_active_chunk(self, chunk_key: ChunkKey, bandwidth_bps: float) -> None:
        state = self.active_chunks.get(chunk_key)
        if not state:
            return

        self._remove_chunk_state(chunk_key, state)

        target_node = self.nodes[state.target]
        result = target_node.process_chunk_transfer(
            state.transfer.file_id,
            state.chunk.chunk_id,
            state.source,
            completed_time=self.simulator.now,
            bandwidth_used_bps=bandwidth_bps,
        )

        if not result.success:
            state.transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=state.transfer.file_id,
                source=state.source,
                target=state.target,
                reason="Chunk processing failed",
            )
            self.transfer_operations[state.source].pop(state.transfer.file_id, None)
            target_node.abort_transfer(state.transfer.file_id)
            return
        self._schedule_chunk_commit(chunk_key, state, result.completion_time, bandwidth_bps)

    def _schedule_chunk_commit(
        self,
        chunk_key: ChunkKey,
        state: ActiveChunk,
        completion_time: float,
        bandwidth_bps: float,
    ) -> None:
        pending = PendingChunkCommit(
            chunk_key=chunk_key,
            source=state.source,
            target=state.target,
            transfer=state.transfer,
            chunk=state.chunk,
            completion_time=completion_time,
            bandwidth_bps=bandwidth_bps,
        )
        self._pending_disk_commits[chunk_key] = pending
        if completion_time <= self.simulator.now:
            self._complete_chunk_commit_event(chunk_key)
        else:
            self.simulator.schedule_at(completion_time, self._complete_chunk_commit_event, chunk_key)

    def _complete_chunk_commit_event(self, chunk_key: ChunkKey) -> None:
        pending = self._pending_disk_commits.pop(chunk_key, None)
        if not pending:
            return
        target_node = self.nodes.get(pending.target)
        if not target_node:
            pending.transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=pending.transfer.file_id,
                source=pending.source,
                target=pending.target,
                reason="Target node unavailable during disk commit",
            )
            self.transfer_operations[pending.source].pop(pending.transfer.file_id, None)
            return

        success = target_node.finalize_chunk_commit(
            pending.transfer.file_id,
            pending.chunk.chunk_id,
            completed_time=self.simulator.now,
        )
        if not success:
            pending.transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=pending.transfer.file_id,
                source=pending.source,
                target=pending.target,
                reason="Disk commit failed",
            )
            self.transfer_operations[pending.source].pop(pending.transfer.file_id, None)
            self._maybe_expand_cluster(pending.target)
            return

        self._emit_event(
            "chunk_completed",
            file_id=pending.transfer.file_id,
            chunk_id=pending.chunk.chunk_id,
            source=pending.source,
            target=pending.target,
        )

        if pending.transfer.status == TransferStatus.COMPLETED:
            self._finalize_transfer(
                pending.source,
                pending.target,
                pending.transfer.file_id,
                pending.transfer,
            )
        else:
            self._schedule_next_chunk(pending.source, pending.target, pending.transfer.file_id)

    def _fail_active_chunk(self, chunk_key: ChunkKey, reason: Optional[str]) -> None:
        state = self.active_chunks.get(chunk_key)
        if not state:
            return

        self._remove_chunk_state(chunk_key, state)
        state.transfer.status = TransferStatus.FAILED
        self._emit_event(
            "transfer_failed",
            file_id=state.transfer.file_id,
            source=state.source,
            target=state.target,
            reason=reason or "Transfer failed",
        )
        self.transfer_operations[state.source].pop(state.transfer.file_id, None)
        target_node = self.nodes.get(state.target)
        if target_node:
            target_node.abort_transfer(state.transfer.file_id)

    def _remove_chunk_state(self, chunk_key: ChunkKey, state: ActiveChunk) -> None:
        self._detach_chunk_from_link(chunk_key, state)
        self.active_chunks.pop(chunk_key, None)
        self.chunk_bandwidths.pop(chunk_key, None)

    def _start_chunk_hop(self, state: ActiveChunk) -> bool:
        source, _ = state.current_hop_nodes()
        node = self.nodes.get(source)
        if not node:
            return False
        if state.hop_index == 0 and state.transfer.is_retrieval:
            if not node.prepare_chunk_read(state.transfer, state.chunk):
                return False
        pid = node.start_chunk_transmission(state.chunk.size)
        if pid is None:
            return False
        state.os_pid = pid
        return True

    def _finish_chunk_hop(self, state: ActiveChunk) -> None:
        if state.os_pid is None:
            return
        source, _ = state.current_hop_nodes()
        node = self.nodes.get(source)
        if node:
            node.complete_chunk_transmission(state.os_pid)
        state.os_pid = None

    def _recalculate_all_link_shares(self) -> None:
        for source_node_id, target_node_id in list(self.link_active_chunks.keys()):
            self._recalculate_link_share(source_node_id, target_node_id)
        for node_id in self.nodes:
            if node_id not in self.node_active_chunks:
                self.nodes[node_id].network_utilization = 0.0

    def _handle_link_failure(self, node1_id: str, node2_id: str) -> None:
        for link in (self._link_key(node1_id, node2_id), self._link_key(node2_id, node1_id)):
            affected = list(self.link_active_chunks.get(link, []))
            for chunk_key in affected:
                self._reroute_or_fail_chunk(chunk_key, reason=f"Link {node1_id}-{node2_id} failed")

    def _handle_node_failure(self, node_id: str) -> None:
        self.node_active_chunks.pop(node_id, None)
        for chunk_key, state in list(self.active_chunks.items()):
            if state.source == node_id or state.target == node_id:
                self._fail_active_chunk(chunk_key, f"Node {node_id} failed")
                continue
            if node_id in state.path:
                self._reroute_or_fail_chunk(chunk_key, reason=f"Node {node_id} failed")
        for chunk_key, pending in list(self._pending_disk_commits.items()):
            if pending.source == node_id or pending.target == node_id:
                self._pending_disk_commits.pop(chunk_key, None)
                pending.transfer.status = TransferStatus.FAILED
                self._emit_event(
                    "transfer_failed",
                    file_id=pending.transfer.file_id,
                    source=pending.source,
                    target=pending.target,
                    reason=f"Node {node_id} failed during disk commit",
                )
                self.transfer_operations[pending.source].pop(pending.transfer.file_id, None)

    def _reroute_or_fail_chunk(self, chunk_key: ChunkKey, reason: str) -> None:
        state = self.active_chunks.get(chunk_key)
        if not state:
            return
        self._detach_chunk_from_link(chunk_key, state)
        new_route = self._compute_route(state.source, state.target)
        if not new_route or len(new_route) < 2:
            self._fail_active_chunk(chunk_key, reason)
            return
        state.path = new_route
        state.hop_index = 0
        if not self._attach_chunk_to_link(chunk_key, state):
            self._fail_active_chunk(chunk_key, reason)

    def _recalculate_link_share(self, source_node_id: str, target_node_id: str) -> None:
        link_key = self._link_key(source_node_id, target_node_id)
        chunk_keys = self.link_active_chunks.get(link_key)
        if not chunk_keys:
            self._update_node_bandwidth(source_node_id)
            self._update_node_bandwidth(target_node_id)
            self._maybe_expand_cluster(source_node_id)
            self._maybe_expand_cluster(target_node_id)
            return

        capacity = self._link_capacity(source_node_id, target_node_id)
        share = capacity / len(chunk_keys) if chunk_keys else 0.0

        for chunk_key in chunk_keys:
            self.chunk_bandwidths[chunk_key] = share

        self._update_node_bandwidth(source_node_id)
        self._update_node_bandwidth(target_node_id)
        self._maybe_expand_cluster(source_node_id)
        self._maybe_expand_cluster(target_node_id)

    def _update_node_bandwidth(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        if not node:
            return
        chunk_keys = self.node_active_chunks.get(node_id)
        if not chunk_keys:
            node.network_utilization = 0.0
            return

        node.network_utilization = sum(self.chunk_bandwidths.get(chunk_key, 0.0) for chunk_key in chunk_keys)

    def _finalize_transfer(
        self,
        source_node_id: str,
        target_node_id: str,
        file_id: str,
        transfer: FileTransfer,
    ) -> None:
        self._register_file_aliases(transfer)
        self._emit_event(
            "transfer_completed",
            file_id=file_id,
            source=source_node_id,
            target=target_node_id,
            completed_at=transfer.completed_at,
        )

        if file_id in self.transfer_operations[source_node_id]:
            del self.transfer_operations[source_node_id][file_id]

        self._ensure_replica_coverage(target_node_id)

        if not transfer.is_retrieval:
            self._replicate_across_cluster(target_node_id, transfer)

        for replica_id in self._get_replica_children(target_node_id):
            self._schedule_replica_seed(target_node_id, replica_id)