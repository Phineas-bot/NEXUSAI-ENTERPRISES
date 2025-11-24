from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional


class ProcessState(Enum):
    READY = auto()
    RUNNING = auto()
    BLOCKED = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class VirtualProcess:
    pid: int
    name: str
    cpu_required: float
    memory_required: int
    target: Callable[[], None]
    state: ProcessState = ProcessState.READY
    cpu_used: float = 0.0
    failure_reason: Optional[str] = None
    work_executed: bool = False


class VirtualOS:
    def __init__(
        self,
        cpu_capacity: float,
        memory_capacity_bytes: int,
        cpu_time_slice: float = 0.01,
    ) -> None:
        self.cpu_capacity = cpu_capacity
        self.memory_capacity_bytes = memory_capacity_bytes
        self.cpu_time_slice = cpu_time_slice
        self._processes: Dict[int, VirtualProcess] = {}
        self._ready_queue: List[int] = []
        self._blocked: List[int] = []
        self._next_pid = 1
        self._used_memory = 0

    @property
    def used_memory(self) -> int:
        return self._used_memory

    def spawn_process(
        self,
        name: str,
        cpu_required: float,
        memory_required: int,
        target: Callable[[], None],
    ) -> Optional[int]:
        if memory_required + self._used_memory > self.memory_capacity_bytes:
            return None
        pid = self._next_pid
        self._next_pid += 1
        process = VirtualProcess(
            pid=pid,
            name=name,
            cpu_required=cpu_required,
            memory_required=memory_required,
            target=target,
        )
        self._processes[pid] = process
        self._ready_queue.append(pid)
        self._used_memory += memory_required
        return pid

    def schedule_tick(self) -> None:
        if not self._ready_queue:
            return
        pid = self._ready_queue.pop(0)
        process = self._processes.get(pid)
        if not process or process.state in (ProcessState.COMPLETED, ProcessState.FAILED):
            return

        process.state = ProcessState.RUNNING
        cpu_budget = min(self.cpu_time_slice, process.cpu_required - process.cpu_used)
        try:
            if not process.work_executed:
                process.target()
                process.work_executed = True
            process.cpu_used += cpu_budget
            if process.cpu_used >= process.cpu_required:
                process.state = ProcessState.COMPLETED
                self._used_memory -= process.memory_required
            else:
                process.state = ProcessState.READY
                self._ready_queue.append(pid)
        except Exception as exc:  # pragma: no cover - best effort
            process.state = ProcessState.FAILED
            process.failure_reason = str(exc)
            self._used_memory -= process.memory_required

    def block_process(self, pid: int) -> None:
        process = self._processes.get(pid)
        if not process or process.state not in (ProcessState.READY, ProcessState.RUNNING):
            return
        process.state = ProcessState.BLOCKED
        if pid in self._ready_queue:
            self._ready_queue.remove(pid)
        self._blocked.append(pid)

    def unblock_process(self, pid: int) -> None:
        if pid in self._blocked:
            self._blocked.remove(pid)
        process = self._processes.get(pid)
        if not process or process.state != ProcessState.BLOCKED:
            return
        process.state = ProcessState.READY
        self._ready_queue.append(pid)

    def kill_process(self, pid: int) -> None:
        process = self._processes.pop(pid, None)
        if not process:
            return
        if pid in self._ready_queue:
            self._ready_queue.remove(pid)
        if pid in self._blocked:
            self._blocked.remove(pid)
        if process.state != ProcessState.COMPLETED:
            self._used_memory -= process.memory_required
        process.state = ProcessState.FAILED

    def has_runnable_work(self) -> bool:
        return bool(self._ready_queue)

    def get_process(self, pid: int) -> Optional[VirtualProcess]:
        return self._processes.get(pid)
