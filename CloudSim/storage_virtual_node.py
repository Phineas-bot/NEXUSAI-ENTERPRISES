import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple, Union
from enum import Enum, auto
import hashlib

from virtual_disk import DiskCorruptionError, DiskIOProfile, DiskIOTicket, VirtualDisk
from virtual_os import ProcessState, VirtualOS, SyscallContext, SyscallResult

if TYPE_CHECKING:  # pragma: no cover
    from simulator import Simulator

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
    is_retrieval: bool = False
    backing_file_id: Optional[str] = None
    target_node: Optional[str] = None
    segment_offset: int = 0

    def __post_init__(self) -> None:
        if self.backing_file_id is None:
            self.backing_file_id = self.file_id

@dataclass
class NetworkInterface:
    name: str
    ip_address: Optional[str] = None
    subnet: Optional[str] = None
    mac_address: Optional[str] = None
    metrics: Dict[str, Union[int, float]] = field(default_factory=dict)


@dataclass
class ChunkCommitResult:
    success: bool
    completion_time: float


@dataclass
class PendingDiskWrite:
    ticket: DiskIOTicket
    chunk: "FileChunk"
    transfer: "FileTransfer"
    source_node: str
    bandwidth_bps: float


class StorageVirtualNode:
    _CPU_SECONDS_PER_MB = 0.002  # Tunable constant representing CPU seconds needed per MB processed
    _WORKING_SET_FRACTION = 0.05  # Percent of total memory to reserve per active chunk (capped by chunk size)
    _MIN_WORKING_SET_BYTES = 4 * 1024 * 1024

    _MIN_CHUNK_SIZE_BYTES = 256 * 1024
    _MAX_CHUNK_SIZE_BYTES = 32 * 1024 * 1024

    def __init__(
        self,
        node_id: str,
        cpu_capacity: int,  # in vCPUs
        memory_capacity: int,  # in GB
        storage_capacity: int,  # in GB
        bandwidth: int,  # in Mbps
        *,
        zone: Optional[str] = None,
    ):
        self.node_id = node_id
        self.cpu_capacity = cpu_capacity
        self.memory_capacity = memory_capacity
        self.total_storage = int(storage_capacity * 1024 * 1024 * 1024)  # Convert GB to bytes
        self.memory_capacity_bytes = max(1, int(memory_capacity * 1024 * 1024 * 1024))
        self.bandwidth = int(bandwidth * 1_000_000)  # Convert Mbps to bits per second
        self.zone = zone
        self.ip_address: Optional[str] = None
        self.network_interfaces: Dict[str, NetworkInterface] = {}
        self.link_latencies: Dict[str, float] = {}
        
        # Current utilization
        self.active_transfers: Dict[str, FileTransfer] = {}
        self.stored_files: Dict[str, FileTransfer] = {}
        self.network_utilization = 0  # Current bandwidth usage
        self.disk_profile = DiskIOProfile()
        self.disk = VirtualDisk(self.total_storage, io_profile=self.disk_profile)
        self.simulator: Optional[Simulator] = None
        self.virtual_os = VirtualOS(
            cpu_capacity=max(1, self.cpu_capacity),
            memory_capacity_bytes=self.memory_capacity_bytes,
            cpu_time_slice=0.01,
        )
        self._disk_device_name = f"disk:{self.node_id}"
        self._network_device_name = f"nic:{self.node_id}"
        self._maintenance_device_name = f"maintenance:{self.node_id}"
        self._transmission_tickets: Dict[int, Optional[int]] = {}
        self._maintenance_tickets: Dict[int, Optional[int]] = {}
        self._background_jobs: Dict[str, List[int]] = {}
        self._register_virtual_os_devices()
        
        # Performance metrics
        self.total_requests_processed = 0
        self.total_data_transferred = 0  # in bytes
        self.failed_transfers = 0
        self.os_process_failures = 0
        
        # Network connections (node_id: bandwidth_available)
        self.connections: Dict[str, int] = {}
        self._pending_disk_writes: Dict[Tuple[str, int], PendingDiskWrite] = {}

    def add_connection(self, node_id: str, bandwidth: int, latency_ms: float = 0.0):
        """Add a network connection to another node"""
        self.connections[node_id] = bandwidth * 1000000  # Store in bits per second
        self.link_latencies[node_id] = max(0.0, latency_ms)

    def attach_simulator(self, simulator: "Simulator") -> None:
        self.simulator = simulator

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
        zone: Optional[str] = None,
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
            zone=zone or self.zone,
        )
        return replica

    def _calculate_chunk_size(self, file_size: int, chunk_size_hint: Optional[int] = None) -> int:
        """Determine optimal chunk size based on file size"""
        if chunk_size_hint is not None:
            # Clamp external hints to safe bounds and total file size
            normalized = max(self._MIN_CHUNK_SIZE_BYTES, min(chunk_size_hint, self._MAX_CHUNK_SIZE_BYTES))
            return max(1, min(normalized, file_size))
        # Simple heuristic: larger files get larger chunks
        if file_size < 10 * 1024 * 1024:  # < 10MB
            return 512 * 1024  # 512KB chunks
        if file_size < 100 * 1024 * 1024:  # < 100MB
            return 2 * 1024 * 1024  # 2MB chunks
        return 10 * 1024 * 1024  # 10MB chunks

    def _generate_chunks(self, file_id: str, file_size: int, chunk_size_hint: Optional[int] = None) -> List[FileChunk]:
        """Break file into chunks for transfer"""
        chunk_size = self._calculate_chunk_size(file_size, chunk_size_hint)
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
        source_node: Optional[str] = None,
        *,
        preferred_chunk_size: Optional[int] = None,
        backing_file_id: Optional[str] = None,
        segment_offset: int = 0,
    ) -> Optional[FileTransfer]:
        """Initiate a file storage request to this node"""
        # Reserve disk capacity ahead of time so transfers cannot overcommit storage
        file_path = f"/{self.node_id}/{file_name}"
        if not self.disk.reserve_file(file_id, file_size, path=file_path):
            return None
        
        # Create file transfer record
        chunks = self._generate_chunks(file_id, file_size, preferred_chunk_size)
        transfer = FileTransfer(
            file_id=file_id,
            file_name=file_name,
            total_size=file_size,
            chunks=chunks,
            created_at=current_time,
            target_node=self.node_id,
            backing_file_id=backing_file_id,
            segment_offset=segment_offset,
        )
        
        self.active_transfers[file_id] = transfer
        return transfer

    @property
    def free_storage(self) -> int:
        return self.disk.free_bytes

    def process_chunk_transfer(
        self,
        file_id: str,
        chunk_id: int,
        source_node: str,
        completed_time: float,
        bandwidth_used_bps: int,
    ) -> ChunkCommitResult:
        """Process an incoming file chunk and return its disk commit schedule."""
        if file_id not in self.active_transfers:
            return ChunkCommitResult(False, completed_time)

        transfer = self.active_transfers[file_id]

        try:
            chunk = next(c for c in transfer.chunks if c.chunk_id == chunk_id)
        except StopIteration:
            return ChunkCommitResult(False, completed_time)

        chunk.stored_node = self.node_id
        chunk.status = TransferStatus.IN_PROGRESS

        if not self._execute_chunk_process(chunk.size, purpose="ingest", work=None):
            self.abort_transfer(file_id)
            return ChunkCommitResult(False, completed_time)

        try:
            ticket = self.disk.schedule_write(
                file_id,
                chunk_id,
                chunk.size,
                current_time=completed_time,
            )
        except Exception:
            self.abort_transfer(file_id)
            return ChunkCommitResult(False, completed_time)

        self._pending_disk_writes[(file_id, chunk_id)] = PendingDiskWrite(
            ticket=ticket,
            chunk=chunk,
            transfer=transfer,
            source_node=source_node,
            bandwidth_bps=float(bandwidth_used_bps),
        )
        return ChunkCommitResult(True, ticket.completion_time)

    def finalize_chunk_commit(
        self,
        file_id: str,
        chunk_id: int,
        *,
        completed_time: float,
    ) -> bool:
        pending = self._pending_disk_writes.pop((file_id, chunk_id), None)
        if not pending:
            return False
        try:
            self.disk.complete_write(pending.ticket, data=None)
        except DiskCorruptionError:
            self.os_process_failures += 1
            self.abort_transfer(file_id)
            return False
        except Exception:
            self.os_process_failures += 1
            self.abort_transfer(file_id)
            return False

        pending.chunk.status = TransferStatus.COMPLETED
        transfer = pending.transfer
        transfer.status = TransferStatus.IN_PROGRESS
        self.total_data_transferred += pending.chunk.size

        if all(c.status == TransferStatus.COMPLETED for c in transfer.chunks):
            transfer.status = TransferStatus.COMPLETED
            transfer.completed_at = completed_time
            self.stored_files[file_id] = transfer
            self.active_transfers.pop(file_id, None)
            self.total_requests_processed += 1
        return True

    def abort_transfer(self, file_id: str) -> None:
        """Abort an in-flight transfer and reclaim its reserved disk space."""
        transfer = self.active_transfers.pop(file_id, None)
        if transfer:
            transfer.status = TransferStatus.FAILED
            self.failed_transfers += 1
        for key, pending in list(self._pending_disk_writes.items()):
            pending_file_id, _ = key
            if pending_file_id != file_id:
                continue
            self._pending_disk_writes.pop(key, None)
            self.disk.cancel_ticket(pending.ticket)
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
        timestamp = time.time()

        return FileTransfer(
            file_id=f"retr-{file_id}-{timestamp}",
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
            ],
            is_retrieval=True,
            backing_file_id=file_id,
            created_at=timestamp,
            target_node=destination_node,
        )

    def store_local_file(
        self,
        file_name: str,
        file_size: int,
        *,
        current_time: float,
    ) -> Optional[FileTransfer]:
        """Persist a file directly onto this node without network hops."""

        file_id = hashlib.md5(f"local-{self.node_id}-{file_name}-{current_time}".encode()).hexdigest()
        if not self.disk.reserve_file(file_id, file_size, path=f"/{self.node_id}/{file_name}"):
            return None

        chunks = self._generate_chunks(file_id, file_size)
        transfer = FileTransfer(
            file_id=file_id,
            file_name=file_name,
            total_size=file_size,
            chunks=chunks,
            status=TransferStatus.COMPLETED,
            created_at=current_time,
            completed_at=current_time,
            target_node=self.node_id,
        )

        for chunk in chunks:
            chunk.status = TransferStatus.COMPLETED
            chunk.stored_node = self.node_id
            self.disk.write_chunk(file_id, chunk.chunk_id, data=None, expected_size=chunk.size)

        self.stored_files[file_id] = transfer
        self.total_data_transferred += file_size
        self.total_requests_processed += 1
        return transfer

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
        syscall = self.virtual_os.invoke_syscall(
            "network_send",
            bytes=chunk_size,
        )
        if not syscall.success:
            self.os_process_failures += 1
            return None
        ticket = syscall.metadata.get("ticket")
        pid = self._start_async_chunk_process(
            chunk_size,
            purpose="egress",
            cpu_scale=0.5,
            memory_scale=1.0,
        )
        if pid is None:
            self.virtual_os.complete_device_request(
                self._network_device_name,
                ticket,
                success=False,
                error="chunk-transmission-not-started",
            )
            self.os_process_failures += 1
            return None
        self._transmission_tickets[pid] = ticket
        return pid

    def complete_chunk_transmission(self, pid: Optional[int]) -> None:
        if pid is None:
            return
        ticket = self._transmission_tickets.pop(pid, None)
        process = self.virtual_os.get_process(pid)
        if not process:
            self.virtual_os.complete_device_request(
                self._network_device_name,
                ticket,
                success=False,
                error="missing-egress-process",
            )
            return
        if process.state == ProcessState.FAILED:
            self.os_process_failures += 1
            self.virtual_os.complete_device_request(
                self._network_device_name,
                ticket,
                success=False,
                error="egress-process-failed",
            )
            return
        if process.state != ProcessState.COMPLETED:
            if not self._run_process_to_completion(pid):
                self.virtual_os.kill_process(pid)
                self.os_process_failures += 1
                self.virtual_os.complete_device_request(
                    self._network_device_name,
                    ticket,
                    success=False,
                    error="egress-process-timeout",
                )
                return
        self.virtual_os.complete_device_request(
            self._network_device_name,
            ticket,
            success=True,
        )

    def schedule_background_job(
        self,
        job_name: str,
        *,
        cpu_seconds: float,
        memory_bytes: int,
        task: Callable[[], None],
    ) -> Optional[int]:
        syscall = self.virtual_os.invoke_syscall("maintenance_hook", job_name=job_name)
        if not syscall.success:
            self.os_process_failures += 1
            return None
        ticket = syscall.metadata.get("ticket")
        pid = self.virtual_os.spawn_process(
            name=f"bg-{job_name}-{self.node_id}",
            cpu_required=max(cpu_seconds, 0.001),
            memory_required=max(memory_bytes, 1),
            target=task,
        )
        if pid is None:
            self.virtual_os.complete_device_request(
                self._maintenance_device_name,
                ticket,
                success=False,
                error="background-process-spawn-failed",
            )
            self.os_process_failures += 1
            return None
        self._background_jobs.setdefault(job_name, []).append(pid)
        self._maintenance_tickets[pid] = ticket
        return pid

    def drain_background_jobs(self) -> None:
        for job_name in list(self._background_jobs.keys()):
            for pid in self._background_jobs[job_name]:
                success = self._run_process_to_completion(pid)
                if not success:
                    self.virtual_os.kill_process(pid)
                    self.os_process_failures += 1
                self.virtual_os.complete_device_request(
                    self._maintenance_device_name,
                    self._maintenance_tickets.pop(pid, None),
                    success=success,
                    error=None if success else "background-process-failed",
                )
            self._background_jobs[job_name] = []

    def prepare_chunk_read(self, transfer: FileTransfer, chunk: FileChunk) -> bool:
        if not transfer.is_retrieval:
            return True

        backing_file_id = transfer.backing_file_id or transfer.file_id

        def read_chunk() -> None:
            result = self.virtual_os.invoke_syscall(
                "disk_read",
                file_id=backing_file_id,
                chunk_id=chunk.chunk_id,
                size=chunk.size,
            )
            if not result.success:
                raise RuntimeError(result.error or "disk-read-failed")

        return self._execute_chunk_process(
            chunk.size,
            purpose="egress-read",
            work=read_chunk,
        )

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

    def _register_virtual_os_devices(self) -> None:
        self.virtual_os.register_device(
            self._disk_device_name,
            handler=self._disk_device_handler,
            max_inflight=4,
        )
        self.virtual_os.register_device(
            self._network_device_name,
            handler=self._network_device_handler,
            max_inflight=max(1, int(self.cpu_capacity)),
        )
        self.virtual_os.register_device(
            self._maintenance_device_name,
            handler=self._maintenance_device_handler,
            max_inflight=1,
        )

        self.virtual_os.register_syscall("disk_write", self._sys_disk_write)
        self.virtual_os.register_syscall("disk_read", self._sys_disk_read)
        self.virtual_os.register_syscall("network_send", self._sys_network_send)
        self.virtual_os.register_syscall("maintenance_hook", self._sys_maintenance_hook)

    def _disk_device_handler(self, payload: Dict[str, Union[str, int]]) -> None:
        operation = payload.get("op")
        file_id = str(payload.get("file_id"))
        chunk_id = int(payload.get("chunk_id", 0))
        if operation == "write":
            self.disk.write_chunk(file_id, chunk_id, data=None, expected_size=int(payload.get("size", 0)))
        elif operation == "read":
            self.disk.read_chunk(file_id, chunk_id)
        else:
            raise ValueError(f"Unsupported disk op '{operation}'")

    def _network_device_handler(self, payload: Dict[str, Union[str, int]]) -> Dict[str, Union[str, int]]:
        return payload

    def _maintenance_device_handler(self, payload: Dict[str, Union[str, int]]) -> Dict[str, Union[str, int]]:
        return payload

    def _sys_disk_write(self, ctx: SyscallContext, *, file_id: str, chunk_id: int, size: int) -> SyscallResult:
        return ctx.device_call(
            self._disk_device_name,
            {
                "op": "write",
                "file_id": file_id,
                "chunk_id": chunk_id,
                "size": size,
            },
        )

    def _sys_disk_read(self, ctx: SyscallContext, *, file_id: str, chunk_id: int, size: int) -> SyscallResult:
        return ctx.device_call(
            self._disk_device_name,
            {
                "op": "read",
                "file_id": file_id,
                "chunk_id": chunk_id,
                "size": size,
            },
        )

    def _sys_network_send(self, ctx: SyscallContext, *, bytes: int) -> SyscallResult:
        return ctx.device_call(
            self._network_device_name,
            {
                "bytes": bytes,
                "node": self.node_id,
            },
            mode="reservation",
        )

    def _sys_maintenance_hook(self, ctx: SyscallContext, *, job_name: str) -> SyscallResult:
        return ctx.device_call(
            self._maintenance_device_name,
            {
                "job": job_name,
                "node": self.node_id,
            },
            mode="reservation",
        )