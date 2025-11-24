from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import hashlib
from collections import defaultdict
import heapq

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

    def current_hop_nodes(self) -> Tuple[str, str]:
        return self.path[self.hop_index], self.path[self.hop_index + 1]

    def on_last_hop(self) -> bool:
        return self.hop_index >= len(self.path) - 2

@dataclass
class DemandScalingConfig:
    enabled: bool = False
    storage_utilization_threshold: float = 0.8
    bandwidth_utilization_threshold: float = 0.85
    max_replicas_per_root: int = 3
    replica_storage_factor: float = 1.0
    replica_bandwidth_factor: float = 1.0


class StorageVirtualNetwork:
    def __init__(
        self,
        simulator: Simulator,
        tick_interval: float = 0.01,
        scaling_config: Optional[DemandScalingConfig] = None,
    ):
        self.simulator = simulator
        self.tick_interval = tick_interval
        self.nodes: Dict[str, StorageVirtualNode] = {}
        self.transfer_operations: Dict[str, Dict[str, FileTransfer]] = defaultdict(dict)
        self.transfer_observers: List[Callable[[Dict[str, Any]], None]] = []
        self.scaling = scaling_config or DemandScalingConfig()

        # Concurrent transfer bookkeeping
        self.active_chunks: Dict[ChunkKey, ActiveChunk] = {}
        self.link_active_chunks: Dict[Tuple[str, str], Set[ChunkKey]] = defaultdict(set)
        self.node_active_chunks: Dict[str, Set[ChunkKey]] = defaultdict(set)
        self.chunk_bandwidths: Dict[ChunkKey, float] = defaultdict(float)
        self._tick_scheduled = False

        # Replica/cluster bookkeeping for decentralized scaling
        self.node_roots: Dict[str, str] = {}
        self.cluster_nodes: Dict[str, Set[str]] = defaultdict(set)

        # Network topology and addressing
        self.link_latency_ms: Dict[Tuple[str, str], float] = {}
        self._ip_counter = 1
        
    def add_node(self, node: StorageVirtualNode, root_id: Optional[str] = None):
        """Add a node to the network"""
        if not getattr(node, "ip_address", None):
            node.ip_address = self._allocate_ip()
        self.nodes[node.node_id] = node
        self._register_node_cluster(node.node_id, root_id)
        
    def connect_nodes(self, node1_id: str, node2_id: str, bandwidth: int, latency_ms: float = 1.0):
        """Connect two nodes with specified bandwidth and latency"""
        if node1_id in self.nodes and node2_id in self.nodes:
            self.nodes[node1_id].add_connection(node2_id, bandwidth, latency_ms)
            self.nodes[node2_id].add_connection(node1_id, bandwidth, latency_ms)
            self.link_latency_ms[(node1_id, node2_id)] = latency_ms
            self.link_latency_ms[(node2_id, node1_id)] = latency_ms
            return True
        return False
    
    def initiate_file_transfer(
        self,
        source_node_id: str,
        target_node_id: str,
        file_name: str,
        file_size: int
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
            
        # Generate unique file ID
        file_id = hashlib.md5(f"{file_name}-{self.simulator.now}".encode()).hexdigest()
        
        # Request storage on target node
        transfer = target_node.initiate_file_transfer(
            file_id,
            file_name,
            file_size,
            current_time=self.simulator.now,
            source_node=source_node_id,
        )

        if not transfer and self.scaling.enabled:
            retry_target_id = self._spawn_replica_node(effective_target_id) or effective_target_id
            next_target_id = self._select_storage_node(retry_target_id, file_size)
            if next_target_id and next_target_id in self.nodes:
                target_node = self.nodes[next_target_id]
                transfer = target_node.initiate_file_transfer(
                    file_id,
                    file_name,
                    file_size,
                    current_time=self.simulator.now,
                    source_node=source_node_id,
                )
                effective_target_id = next_target_id

        if transfer:
            self.transfer_operations[source_node_id][file_id] = transfer
            self._schedule_next_chunk(source_node_id, effective_target_id, file_id, route)
            return transfer
        return None
    
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

    def get_route(self, source_node_id: str, target_node_id: str) -> Optional[List[str]]:
        """Expose the currently computed routing path for testing/inspection."""
        return self._compute_route(source_node_id, target_node_id)

    def _emit_event(self, event_type: str, **payload: Any) -> None:
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
        source_node = self.nodes[source_node_id]
        target_node = self.nodes[target_node_id]
        link_bandwidth = min(
            source_node.connections.get(target_node_id, 0),
            target_node.connections.get(source_node_id, 0),
        )
        return float(min(link_bandwidth, source_node.bandwidth, target_node.bandwidth))

    def _neighbor_links(self, node_id: str) -> List[Tuple[str, float]]:
        node = self.nodes[node_id]
        neighbors: List[Tuple[str, float]] = []
        for neighbor_id in node.connections.keys():
            if neighbor_id not in self.nodes:
                continue
            latency = self.link_latency_ms.get((node_id, neighbor_id), 1.0)
            neighbors.append((neighbor_id, latency))
        return neighbors

    def _compute_route(self, source_node_id: str, target_node_id: str) -> Optional[List[str]]:
        if source_node_id == target_node_id:
            return [source_node_id]

        visited: Set[str] = set()
        heap: List[Tuple[float, str, Optional[str]]] = [(0.0, source_node_id, None)]
        parents: Dict[str, Optional[str]] = {}

        while heap:
            cost, node_id, parent = heapq.heappop(heap)
            if node_id in visited:
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

        path: List[str] = []
        current: Optional[str] = target_node_id
        while current is not None:
            path.append(current)
            current = parents.get(current)
        path.reverse()
        return path

    def _ensure_tick(self) -> None:
        if not self._tick_scheduled:
            self._tick_scheduled = True
            self.simulator.schedule_in(0.0, self._network_tick)

    def _attach_chunk_to_link(self, chunk_key: ChunkKey, state: ActiveChunk) -> None:
        source, target = state.current_hop_nodes()
        link_key = self._link_key(source, target)
        self.link_active_chunks[link_key].add(chunk_key)
        self.node_active_chunks[source].add(chunk_key)
        self.node_active_chunks[target].add(chunk_key)
        self._recalculate_link_share(source, target)

    def _detach_chunk_from_link(self, chunk_key: ChunkKey, state: ActiveChunk) -> None:
        source, target = state.current_hop_nodes()
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
        self._attach_chunk_to_link(chunk_key, state)

    def _register_node_cluster(self, node_id: str, root_id: Optional[str] = None) -> None:
        root = root_id or self.node_roots.get(node_id) or node_id
        self.node_roots[node_id] = root
        cluster = self.cluster_nodes.setdefault(root, set())
        cluster.add(node_id)

    def _get_root_id(self, node_id: str) -> str:
        return self.node_roots.get(node_id, node_id)

    def _get_cluster_nodes(self, node_id: str) -> Set[str]:
        root = self._get_root_id(node_id)
        if root not in self.cluster_nodes and root in self.nodes:
            self.cluster_nodes[root] = {root}
            self.node_roots[root] = root
        return self.cluster_nodes.get(root, set())

    def _select_storage_node(self, requested_node_id: str, required_size: Optional[int] = None) -> Optional[str]:
        candidates = [
            self.nodes[node_id]
            for node_id in self._get_cluster_nodes(requested_node_id)
            if node_id in self.nodes
        ]
        if not candidates:
            return None

        def has_capacity(node: StorageVirtualNode) -> bool:
            if required_size is None:
                return True
            projected = node.projected_storage_usage if hasattr(node, "projected_storage_usage") else (node.used_storage + sum(t.total_size for t in node.active_transfers.values()))
            return (projected + required_size) <= node.total_storage

        eligible = [node for node in candidates if has_capacity(node)]
        if not eligible:
            return None

        def projected_usage_ratio(node: StorageVirtualNode) -> float:
            projected = node.projected_storage_usage if hasattr(node, "projected_storage_usage") else (node.used_storage + sum(t.total_size for t in node.active_transfers.values()))
            return (projected / node.total_storage) if node.total_storage else 0.0

        eligible.sort(key=projected_usage_ratio)
        return eligible[0].node_id

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

    def _spawn_replica_node(self, reference_node_id: str) -> Optional[str]:
        if not self.scaling.enabled or reference_node_id not in self.nodes:
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

        for neighbor_id, bandwidth_bps in reference_node.connections.items():
            if neighbor_id not in self.nodes:
                continue
            bandwidth_mbps = max(1, int(bandwidth_bps / 1000000))
            latency = reference_node.get_link_latency(neighbor_id)
            self.connect_nodes(replica_id, neighbor_id, bandwidth=bandwidth_mbps, latency_ms=latency)

        parent_link_bandwidth = max(1, int(reference_node.bandwidth / 1000000))
        self.connect_nodes(replica_id, reference_node.node_id, bandwidth=parent_link_bandwidth, latency_ms=1.0)
        return replica_id

    def _is_node_overloaded(self, node: StorageVirtualNode) -> bool:
        if not self.scaling.enabled:
            return False
        projected = node.projected_storage_usage if hasattr(node, "projected_storage_usage") else node.used_storage
        storage_ratio = (projected / node.total_storage) if node.total_storage else 0.0
        bandwidth_ratio = (node.network_utilization / node.bandwidth) if node.bandwidth else 0.0
        return (
            storage_ratio >= self.scaling.storage_utilization_threshold
            or bandwidth_ratio >= self.scaling.bandwidth_utilization_threshold
        )

    def _maybe_expand_cluster(self, node_id: str) -> None:
        if not self.scaling.enabled or node_id not in self.nodes:
            return
        root_id = self._get_root_id(node_id)
        cluster = self._get_cluster_nodes(root_id)
        if len(cluster) - 1 >= self.scaling.max_replicas_per_root:
            return
        overloaded = [self.nodes[nid] for nid in cluster if nid in self.nodes and self._is_node_overloaded(self.nodes[nid])]
        if not overloaded:
            return
        overloaded.sort(key=lambda n: n.network_utilization, reverse=True)
        self._spawn_replica_node(overloaded[0].node_id)

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
        self._attach_chunk_to_link(chunk_key, state)

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
        success = target_node.process_chunk_transfer(
            state.transfer.file_id,
            state.chunk.chunk_id,
            state.source,
            completed_time=self.simulator.now,
            bandwidth_used_bps=bandwidth_bps,
        )

        if not success:
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

        self._emit_event(
            "chunk_completed",
            file_id=state.transfer.file_id,
            chunk_id=state.chunk.chunk_id,
            source=state.source,
            target=state.target,
        )

        if state.transfer.status == TransferStatus.COMPLETED:
            self._finalize_transfer(state.source, state.target, state.transfer.file_id, state.transfer)
        else:
            self._schedule_next_chunk(state.source, state.target, state.transfer.file_id)

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

    def _recalculate_all_link_shares(self) -> None:
        for source_node_id, target_node_id in list(self.link_active_chunks.keys()):
            self._recalculate_link_share(source_node_id, target_node_id)
        for node_id in self.nodes:
            if node_id not in self.node_active_chunks:
                self.nodes[node_id].network_utilization = 0.0

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
        self._emit_event(
            "transfer_completed",
            file_id=file_id,
            source=source_node_id,
            target=target_node_id,
            completed_at=transfer.completed_at,
        )

        if file_id in self.transfer_operations[source_node_id]:
            del self.transfer_operations[source_node_id][file_id]