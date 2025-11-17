from typing import Any, Callable, Dict, List, Optional, Tuple
import hashlib
from collections import defaultdict

from storage_virtual_node import StorageVirtualNode, FileTransfer, TransferStatus
from simulator import Simulator

class StorageVirtualNetwork:
    def __init__(self, simulator: Simulator):
        self.simulator = simulator
        self.nodes: Dict[str, StorageVirtualNode] = {}
        self.transfer_operations: Dict[str, Dict[str, FileTransfer]] = defaultdict(dict)
        self.transfer_targets: Dict[Tuple[str, str], str] = {}
        self.transfer_observers: List[Callable[[Dict[str, Any]], None]] = []
        
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
            self.transfer_targets[(source_node_id, file_id)] = target_node_id
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

    def _effective_bandwidth(self, source_node_id: str, target_node_id: str) -> int:
        source_node = self.nodes[source_node_id]
        target_node = self.nodes[target_node_id]
        link_bandwidth = min(
            source_node.connections.get(target_node_id, 0),
            target_node.connections.get(source_node_id, 0),
        )
        return int(min(link_bandwidth, source_node.bandwidth, target_node.bandwidth))

    def _schedule_next_chunk(self, source_node_id: str, target_node_id: str, file_id: str) -> None:
        transfer = self.transfer_operations[source_node_id].get(file_id)
        if not transfer:
            return

        next_chunk = next((c for c in transfer.chunks if c.status != TransferStatus.COMPLETED), None)
        if not next_chunk:
            self._finalize_transfer(source_node_id, target_node_id, file_id, transfer)
            return

        bandwidth_bps = self._effective_bandwidth(source_node_id, target_node_id)
        if bandwidth_bps <= 0:
            source_node = self.nodes[source_node_id]
            target_node = self.nodes[target_node_id]
            source_node.network_utilization = 0
            target_node.network_utilization = 0
            transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=file_id,
                source=source_node_id,
                target=target_node_id,
                reason="No available bandwidth",
            )
            return

        duration = (next_chunk.size * 8) / bandwidth_bps
        next_chunk.status = TransferStatus.IN_PROGRESS
        transfer.status = TransferStatus.IN_PROGRESS

        source_node = self.nodes[source_node_id]
        target_node = self.nodes[target_node_id]
        source_node.network_utilization = bandwidth_bps
        target_node.network_utilization = bandwidth_bps

        self.simulator.schedule_in(
            duration,
            self._complete_chunk_transfer,
            source_node_id,
            target_node_id,
            file_id,
            next_chunk.chunk_id,
            bandwidth_bps,
        )

    def _complete_chunk_transfer(
        self,
        source_node_id: str,
        target_node_id: str,
        file_id: str,
        chunk_id: int,
        bandwidth_bps: int,
    ) -> None:
        transfer = self.transfer_operations[source_node_id].get(file_id)
        if not transfer:
            return

        target_node = self.nodes[target_node_id]
        success = target_node.process_chunk_transfer(
            file_id,
            chunk_id,
            source_node_id,
            completed_time=self.simulator.now,
            bandwidth_used_bps=bandwidth_bps,
        )

        if not success:
            source_node = self.nodes[source_node_id]
            target_node = self.nodes[target_node_id]
            source_node.network_utilization = 0
            target_node.network_utilization = 0
            transfer.status = TransferStatus.FAILED
            self._emit_event(
                "transfer_failed",
                file_id=file_id,
                source=source_node_id,
                target=target_node_id,
                reason="Chunk processing failed",
            )
            return

        self._emit_event(
            "chunk_completed",
            file_id=file_id,
            chunk_id=chunk_id,
            source=source_node_id,
            target=target_node_id,
        )

        if transfer.status == TransferStatus.COMPLETED:
            self._finalize_transfer(source_node_id, target_node_id, file_id, transfer)
        else:
            self._schedule_next_chunk(source_node_id, target_node_id, file_id)

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
        source_node = self.nodes[source_node_id]
        target_node = self.nodes[target_node_id]
        source_node.network_utilization = 0
        target_node.network_utilization = 0

        self.transfer_targets.pop((source_node_id, file_id), None)
        if file_id in self.transfer_operations[source_node_id]:
            del self.transfer_operations[source_node_id][file_id]