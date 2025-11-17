from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import hashlib
from collections import defaultdict

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

class StorageVirtualNetwork:
    def __init__(self, simulator: Simulator, tick_interval: float = 0.01):
        self.simulator = simulator
        self.tick_interval = tick_interval
        self.nodes: Dict[str, StorageVirtualNode] = {}
        self.transfer_operations: Dict[str, Dict[str, FileTransfer]] = defaultdict(dict)
        self.transfer_observers: List[Callable[[Dict[str, Any]], None]] = []

        # Concurrent transfer bookkeeping
        self.active_chunks: Dict[ChunkKey, ActiveChunk] = {}
        self.link_active_chunks: Dict[Tuple[str, str], Set[ChunkKey]] = defaultdict(set)
        self.node_active_chunks: Dict[str, Set[ChunkKey]] = defaultdict(set)
        self.chunk_bandwidths: Dict[ChunkKey, float] = defaultdict(float)
        self._tick_scheduled = False
        
    def add_node(self, node: StorageVirtualNode):
        """Add a node to the network"""
        self.nodes[node.node_id] = node
        
    def connect_nodes(self, node1_id: str, node2_id: str, bandwidth: int):
        """Connect two nodes with specified bandwidth"""
        if node1_id in self.nodes and node2_id in self.nodes:
            self.nodes[node1_id].add_connection(node2_id, bandwidth)
            self.nodes[node2_id].add_connection(node1_id, bandwidth)
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
            
        # Generate unique file ID
        file_id = hashlib.md5(f"{file_name}-{self.simulator.now}".encode()).hexdigest()
        
        # Request storage on target node
        target_node = self.nodes[target_node_id]
        transfer = target_node.initiate_file_transfer(
            file_id,
            file_name,
            file_size,
            current_time=self.simulator.now,
            source_node=source_node_id,
        )
        
        if transfer:
            self.transfer_operations[source_node_id][file_id] = transfer
            self._schedule_next_chunk(source_node_id, target_node_id, file_id)
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

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, "time": self.simulator.now, **payload}
        for observer in self.transfer_observers:
            observer(event)

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

    def _ensure_tick(self) -> None:
        if not self._tick_scheduled:
            self._tick_scheduled = True
            self.simulator.schedule_in(0.0, self._network_tick)

    def _schedule_next_chunk(self, source_node_id: str, target_node_id: str, file_id: str) -> None:
        transfer = self.transfer_operations[source_node_id].get(file_id)
        if not transfer:
            return

        next_chunk = next((c for c in transfer.chunks if c.status != TransferStatus.COMPLETED), None)
        if not next_chunk:
            self._finalize_transfer(source_node_id, target_node_id, file_id, transfer)
            return

        if self._link_capacity(source_node_id, target_node_id) <= 0:
            transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=file_id,
                source=source_node_id,
                target=target_node_id,
                reason="No available bandwidth",
            )
            self.transfer_operations[source_node_id].pop(file_id, None)
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
        )

        self.active_chunks[chunk_key] = state
        self.link_active_chunks[self._link_key(source_node_id, target_node_id)].add(chunk_key)
        self.node_active_chunks[source_node_id].add(chunk_key)
        self.node_active_chunks[target_node_id].add(chunk_key)

        next_chunk.status = TransferStatus.IN_PROGRESS
        transfer.status = TransferStatus.IN_PROGRESS

        self._recalculate_link_share(source_node_id, target_node_id)
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
                completed.append((chunk_key, False, "No available bandwidth"))
                continue

            bytes_transferred = share * self.tick_interval / 8
            state.remaining_bytes -= bytes_transferred
            if state.remaining_bytes <= 0:
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

    def _remove_chunk_state(self, chunk_key: ChunkKey, state: ActiveChunk) -> None:
        link_key = self._link_key(state.source, state.target)
        self.active_chunks.pop(chunk_key, None)
        self.chunk_bandwidths.pop(chunk_key, None)

        link_chunks = self.link_active_chunks.get(link_key)
        if link_chunks:
            link_chunks.discard(chunk_key)
            if not link_chunks:
                self.link_active_chunks.pop(link_key, None)

        for node_id in (state.source, state.target):
            node_chunks = self.node_active_chunks.get(node_id)
            if node_chunks:
                node_chunks.discard(chunk_key)
                if not node_chunks:
                    self.node_active_chunks.pop(node_id, None)
            self._update_node_bandwidth(node_id)

        self._recalculate_link_share(state.source, state.target)

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
            return

        capacity = self._link_capacity(source_node_id, target_node_id)
        share = capacity / len(chunk_keys) if chunk_keys else 0.0

        for chunk_key in chunk_keys:
            self.chunk_bandwidths[chunk_key] = share

        self._update_node_bandwidth(source_node_id)
        self._update_node_bandwidth(target_node_id)

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