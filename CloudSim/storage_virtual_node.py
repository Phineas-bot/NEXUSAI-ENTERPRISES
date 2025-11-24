import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Union
from enum import Enum, auto
import hashlib

from virtual_disk import VirtualDisk
from virtual_os import ProcessState, VirtualOS

class TransferStatus(Enum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()

@dataclass
class FileChunk:
    chunk_id: int
    size: int  # in bytes
    checksum: str
    status: TransferStatus = TransferStatus.PENDING
    stored_node: Optional[str] = None

@dataclass
class FileTransfer:
    file_id: str
    file_name: str
    total_size: int  # in bytes
    chunks: List[FileChunk]
    status: TransferStatus = TransferStatus.PENDING
    created_at: float = 0.0
    completed_at: Optional[float] = None

@dataclass
class NetworkInterface:
    name: str
    ip_address: Optional[str] = None
    subnet: Optional[str] = None
    mac_address: Optional[str] = None
    metrics: Dict[str, Union[int, float]] = field(default_factory=dict)


class StorageVirtualNode:
    _CPU_SECONDS_PER_MB = 0.002  # Tunable constant representing CPU seconds needed per MB processed
    _WORKING_SET_FRACTION = 0.05  # Percent of total memory to reserve per active chunk (capped by chunk size)
    _MIN_WORKING_SET_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        node_id: str,
        cpu_capacity: int,  # in vCPUs
        memory_capacity: int,  # in GB
        storage_capacity: int,  # in GB
        bandwidth: int  # in Mbps
    ):
        self.node_id = node_id
        self.cpu_capacity = cpu_capacity
        self.memory_capacity = memory_capacity
        self.total_storage = int(storage_capacity * 1024 * 1024 * 1024)  # Convert GB to bytes
        self.memory_capacity_bytes = max(1, int(memory_capacity * 1024 * 1024 * 1024))
        self.bandwidth = int(bandwidth * 1_000_000)  # Convert Mbps to bits per second
        self.ip_address: Optional[str] = None
        self.network_interfaces: Dict[str, NetworkInterface] = {}
        self.link_latencies: Dict[str, float] = {}
        
        # Current utilization
        self.active_transfers: Dict[str, FileTransfer] = {}
        self.stored_files: Dict[str, FileTransfer] = {}
        self.network_utilization = 0  # Current bandwidth usage
        self.disk = VirtualDisk(self.total_storage)
        self.virtual_os = VirtualOS(
            cpu_capacity=max(1, self.cpu_capacity),
            memory_capacity_bytes=self.memory_capacity_bytes,
            cpu_time_slice=0.01,
        )
        
        # Performance metrics
        self.total_requests_processed = 0
        self.total_data_transferred = 0  # in bytes
        self.failed_transfers = 0
        self.os_process_failures = 0
        
        # Network connections (node_id: bandwidth_available)
        self.connections: Dict[str, int] = {}

    def add_connection(self, node_id: str, bandwidth: int, latency_ms: float = 0.0):
        """Add a network connection to another node"""
        self.connections[node_id] = bandwidth * 1000000  # Store in bits per second
        self.link_latencies[node_id] = max(0.0, latency_ms)

    def get_link_latency(self, node_id: str) -> float:
        return self.link_latencies.get(node_id, 0.0)

    def set_ip_address(self, ip_address: str) -> None:
        self.ip_address = ip_address

    def add_interface(
        self,
        name: str,
        ip_address: Optional[str] = None,
        subnet: Optional[str] = None,
        mac_address: Optional[str] = None,
    ) -> NetworkInterface:
        iface = NetworkInterface(name=name, ip_address=ip_address, subnet=subnet, mac_address=mac_address)
        self.network_interfaces[name] = iface
        if ip_address and not self.ip_address:
            self.ip_address = ip_address
        return iface

    def get_interface(self, name: str) -> Optional[NetworkInterface]:
        return self.network_interfaces.get(name)

    def clone(
        self,
        node_id: str,
        storage_factor: float = 1.0,
        bandwidth_factor: float = 1.0,
    ) -> "StorageVirtualNode":
        """Create a replica node with proportional resources."""
        storage_gb = max(1, math.ceil((self.total_storage / (1024 ** 3)) * storage_factor))
        bandwidth_mbps = max(1, math.ceil((self.bandwidth / 1000000) * bandwidth_factor))
        replica = StorageVirtualNode(
            node_id,
            cpu_capacity=self.cpu_capacity,
            memory_capacity=self.memory_capacity,
            storage_capacity=storage_gb,
            bandwidth=bandwidth_mbps,
        )
        return replica

    def _calculate_chunk_size(self, file_size: int) -> int:
        """Determine optimal chunk size based on file size"""
        # Simple heuristic: larger files get larger chunks
        if file_size < 10 * 1024 * 1024:  # < 10MB
            return 512 * 1024  # 512KB chunks
        elif file_size < 100 * 1024 * 1024:  # < 100MB
            return 2 * 1024 * 1024  # 2MB chunks
        else:
            return 10 * 1024 * 1024  # 10MB chunks

    def _generate_chunks(self, file_id: str, file_size: int) -> List[FileChunk]:
        """Break file into chunks for transfer"""
        chunk_size = self._calculate_chunk_size(file_size)
        num_chunks = math.ceil(file_size / chunk_size)
        
        chunks = []
        for i in range(num_chunks):
            # In a real system, we'd compute actual checksums
            fake_checksum = hashlib.md5(f"{file_id}-{i}".encode()).hexdigest()
            actual_chunk_size = min(chunk_size, file_size - i * chunk_size)
            chunks.append(FileChunk(
                chunk_id=i,
                size=actual_chunk_size,
                checksum=fake_checksum
            ))
        
        return chunks

    def initiate_file_transfer(
        self,
        file_id: str,
        file_name: str,
        file_size: int,
        current_time: float,
        source_node: Optional[str] = None
    ) -> Optional[FileTransfer]:
        """Initiate a file storage request to this node"""
        # Reserve disk capacity ahead of time so transfers cannot overcommit storage
        if not self.disk.reserve_file(file_id, file_size):
            return None
        
        # Create file transfer record
        chunks = self._generate_chunks(file_id, file_size)
        transfer = FileTransfer(
            file_id=file_id,
            file_name=file_name,
            total_size=file_size,
            chunks=chunks,
            created_at=current_time
        )
        
        self.active_transfers[file_id] = transfer
        return transfer

    def process_chunk_transfer(
        self,
        file_id: str,
        chunk_id: int,
        source_node: str,
        completed_time: float,
        bandwidth_used_bps: int
    ) -> bool:
        """Process an incoming file chunk"""
        if file_id not in self.active_transfers:
            return False
        
        transfer = self.active_transfers[file_id]
        
        try:
            chunk = next(c for c in transfer.chunks if c.chunk_id == chunk_id)
        except StopIteration:
            return False
        
        chunk.stored_node = self.node_id
        chunk.status = TransferStatus.IN_PROGRESS

        def commit_chunk() -> None:
            self.disk.write_chunk(file_id, chunk_id, data=None, expected_size=chunk.size)

        # Simulate CPU/memory consumption through the virtual OS (includes disk commit)
        if not self._execute_chunk_process(chunk.size, purpose="ingest", work=commit_chunk):
            self.abort_transfer(file_id)
            return False

        chunk.status = TransferStatus.COMPLETED
        
        # Update metrics
        transfer.status = TransferStatus.IN_PROGRESS
        self.total_data_transferred += chunk.size
        
        # Check if all chunks are completed
        if all(c.status == TransferStatus.COMPLETED for c in transfer.chunks):
            transfer.status = TransferStatus.COMPLETED
            transfer.completed_at = completed_time
            self.stored_files[file_id] = transfer
            del self.active_transfers[file_id]
            self.total_requests_processed += 1
        
        return True

    def abort_transfer(self, file_id: str) -> None:
        """Abort an in-flight transfer and reclaim its reserved disk space."""
        transfer = self.active_transfers.pop(file_id, None)
        if transfer:
            transfer.status = TransferStatus.FAILED
            self.failed_transfers += 1
        self.disk.release_file(file_id)

    def retrieve_file(
        self,
        file_id: str,
        destination_node: str
    ) -> Optional[FileTransfer]:
        """Initiate file retrieval to another node"""
        if file_id not in self.stored_files:
            return None
        
        file_transfer = self.stored_files[file_id]
        
        # Create a new transfer record for the retrieval
        new_transfer = FileTransfer(
            file_id=f"retr-{file_id}-{time.time()}",
            file_name=file_transfer.file_name,
            total_size=file_transfer.total_size,
            chunks=[
                FileChunk(
                    chunk_id=c.chunk_id,
                    size=c.size,
                    checksum=c.checksum,
                    stored_node=destination_node
                )
                for c in file_transfer.chunks
            ]
        )
        
        return new_transfer

    @property
    def used_storage(self) -> int:
        return self.disk.used_bytes

    @property
    def projected_storage_usage(self) -> int:
        return self.disk.used_bytes + self.disk.reserved_bytes

    def get_storage_utilization(self) -> Dict[str, Union[int, float, List[str]]]:
        """Get current storage utilization metrics"""
        utilization = (self.disk.used_bytes / self.total_storage) * 100 if self.total_storage else 0.0
        return {
            "used_bytes": self.disk.used_bytes,
            "reserved_bytes": self.disk.reserved_bytes,
            "total_bytes": self.total_storage,
            "utilization_percent": utilization,
            "files_stored": len(self.stored_files),
            "active_transfers": len(self.active_transfers),
        }

    def get_network_utilization(self) -> Dict[str, Union[int, float, List[str]]]:
        """Get current network utilization metrics"""
        total_bandwidth_bps = self.bandwidth
        return {
            "current_utilization_bps": self.network_utilization,  # float
            "max_bandwidth_bps": total_bandwidth_bps,  # int
            "utilization_percent": (self.network_utilization / total_bandwidth_bps) * 100,  # float
            "connections": list(self.connections.keys())  # List[str]
        }

    def get_performance_metrics(self) -> Dict[str, int]:
        """Get node performance metrics"""
        return {
            "total_requests_processed": self.total_requests_processed,
            "total_data_transferred_bytes": self.total_data_transferred,
            "failed_transfers": self.failed_transfers,
            "current_active_transfers": len(self.active_transfers),
            "os_used_memory_bytes": self.virtual_os.used_memory,
            "os_process_failures": self.os_process_failures,
        }

    def os_tick(self) -> None:
        """Advance the virtual OS scheduler according to CPU capacity."""
        timeslices = max(1, int(self.cpu_capacity))
        for _ in range(timeslices):
            if not self.virtual_os.has_runnable_work():
                break
            self.virtual_os.schedule_tick()

    def start_chunk_transmission(self, chunk_size: int) -> Optional[int]:
        """Spawn a non-blocking OS process to govern outbound chunk handling."""
        pid = self._start_async_chunk_process(
            chunk_size,
            purpose="egress",
            cpu_scale=0.5,
            memory_scale=1.0,
        )
        if pid is None:
            self.os_process_failures += 1
        return pid

    def complete_chunk_transmission(self, pid: Optional[int]) -> None:
        if pid is None:
            return
        process = self.virtual_os.get_process(pid)
        if not process:
            return
        if process.state == ProcessState.FAILED:
            self.os_process_failures += 1
            return
        if process.state == ProcessState.COMPLETED:
            return
        if not self._run_process_to_completion(pid):
            self.virtual_os.kill_process(pid)
            self.os_process_failures += 1

    def _execute_chunk_process(
        self,
        chunk_size: int,
        *,
        purpose: str,
        cpu_scale: float = 1.0,
        memory_scale: float = 1.0,
        work: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Reserve CPU/memory via the VirtualOS before committing data."""
        pid = self.virtual_os.spawn_process(
            name=f"{purpose}-{self.node_id}",
            cpu_required=self._compute_cpu_requirement(chunk_size, cpu_scale),
            memory_required=self._compute_memory_requirement(chunk_size, memory_scale),
            target=work if work is not None else (lambda: None),
        )
        if pid is None:
            self.os_process_failures += 1
            return False

        if not self._run_process_to_completion(pid):
            self.virtual_os.kill_process(pid)
            self.os_process_failures += 1
            return False
        return True

    def _start_async_chunk_process(
        self,
        chunk_size: int,
        *,
        purpose: str,
        cpu_scale: float = 1.0,
        memory_scale: float = 1.0,
    ) -> Optional[int]:
        pid = self.virtual_os.spawn_process(
            name=f"{purpose}-{self.node_id}",
            cpu_required=self._compute_cpu_requirement(chunk_size, cpu_scale),
            memory_required=self._compute_memory_requirement(chunk_size, memory_scale),
            target=lambda: None,
        )
        if pid is None:
            return None
        return pid

    def _compute_memory_requirement(self, chunk_size: int, scale: float) -> int:
        working_set = min(int(self.memory_capacity_bytes * self._WORKING_SET_FRACTION), chunk_size)
        working_set = max(working_set, min(self._MIN_WORKING_SET_BYTES, self.memory_capacity_bytes))
        return max(1, int(working_set * max(scale, 0.01)))

    def _compute_cpu_requirement(self, chunk_size: int, scale: float) -> float:
        base = max(
            0.001,
            (chunk_size / (1024 * 1024)) * self._CPU_SECONDS_PER_MB / max(1, self.cpu_capacity),
        )
        return max(0.001, base * max(scale, 0.01))

    def _run_process_to_completion(self, pid: int, max_ticks: int = 10_000) -> bool:
        for _ in range(max_ticks):
            process = self.virtual_os.get_process(pid)
            if not process:
                return False
            if process.state == ProcessState.COMPLETED:
                return True
            if process.state == ProcessState.FAILED:
                return False
            self.virtual_os.schedule_tick()
        return False