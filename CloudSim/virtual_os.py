from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional


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


@dataclass
class SyscallResult:
    success: bool
    result: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceInterrupt:
    device_name: str
    status: str
    payload: Dict[str, Any]
    result: Any = None
    error: Optional[str] = None


@dataclass
class DeviceRequest:
    request_id: int
    payload: Dict[str, Any]
    mode: str


@dataclass
class DeviceSubmitResult:
    accepted: bool
    result: Any = None
    error: Optional[str] = None
    reason: Optional[str] = None
    interrupt: Optional[DeviceInterrupt] = None
    ticket: Optional[int] = None


class SyscallContext:
    def __init__(self, os_ref: "VirtualOS") -> None:
        self._os = os_ref

    def device_call(
        self,
        device_name: str,
        payload: Dict[str, Any],
        *,
        mode: str = "instant",
    ) -> SyscallResult:
        submit = self._os._submit_device_request(device_name, payload, mode=mode)
        metadata = {"device": device_name}
        if submit.ticket is not None:
            metadata["ticket"] = submit.ticket
        if not submit.accepted:
            error = submit.reason or submit.error or "device-busy"
            return SyscallResult(False, error=error, metadata=metadata)
        if submit.interrupt is not None:
            self._os._enqueue_interrupt(submit.interrupt)
        if submit.error:
            return SyscallResult(False, error=submit.error, metadata=metadata)
        return SyscallResult(True, result=submit.result, metadata=metadata)


class VirtualDevice:
    def __init__(
        self,
        name: str,
        handler: Optional[Callable[[Dict[str, Any]], Any]],
        max_inflight: int,
    ) -> None:
        self.name = name
        self._handler = handler
        self.max_inflight = max(1, int(max_inflight))
        self._inflight = 0
        self._next_request_id = 1
        self._active_requests: Dict[int, DeviceRequest] = {}

    def submit(self, payload: Dict[str, Any], *, mode: str = "instant") -> DeviceSubmitResult:
        mode = mode.lower()
        if mode not in {"instant", "reservation"}:
            raise ValueError("mode must be 'instant' or 'reservation'")
        if self._inflight >= self.max_inflight:
            return DeviceSubmitResult(accepted=False, reason="saturated")

        request_id = self._next_request_id
        self._next_request_id += 1
        request = DeviceRequest(request_id=request_id, payload=payload, mode=mode)
        self._inflight += 1

        if mode == "reservation":
            self._active_requests[request_id] = request
            return DeviceSubmitResult(accepted=True, ticket=request_id)

        return self._complete_instant_request(request)

    def _complete_instant_request(self, request: DeviceRequest) -> DeviceSubmitResult:
        error = None
        result: Any = None
        try:
            if self._handler is not None:
                result = self._handler(request.payload)
        except Exception as exc:  # pragma: no cover - best effort
            error = str(exc)
        finally:
            self._inflight = max(0, self._inflight - 1)

        interrupt = DeviceInterrupt(
            device_name=self.name,
            status="error" if error else "ok",
            payload=request.payload,
            result=result,
            error=error,
        )
        return DeviceSubmitResult(
            accepted=True,
            result=result,
            error=error,
            interrupt=interrupt,
        )

    def complete(
        self,
        request_id: int,
        success: bool = True,
        error: Optional[str] = None,
        result: Any = None,
    ) -> DeviceInterrupt:
        request = self._active_requests.pop(request_id, None)
        if request is None:
            raise KeyError(f"request {request_id} not found for device {self.name}")
        self._inflight = max(0, self._inflight - 1)
        status = "ok" if success and not error else "error"
        return DeviceInterrupt(
            device_name=self.name,
            status=status,
            payload=request.payload,
            result=result,
            error=error,
        )

    @property
    def inflight(self) -> int:
        return self._inflight


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
        self._devices: Dict[str, VirtualDevice] = {}
        self._syscalls: Dict[str, Callable[[SyscallContext], SyscallResult]] = {}
        self._interrupt_handlers: Dict[str, List[Callable[[DeviceInterrupt], None]]] = defaultdict(list)
        self._interrupt_queue: List[DeviceInterrupt] = []
        self._syscall_invocations = 0
        self._syscall_denials = 0

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
            self.process_interrupts()
            return
        pid = self._ready_queue.pop(0)
        process = self._processes.get(pid)
        if not process or process.state in (ProcessState.COMPLETED, ProcessState.FAILED):
            self.process_interrupts()
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
        finally:
            self.process_interrupts()

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

    def register_device(
        self,
        name: str,
        *,
        handler: Optional[Callable[[Dict[str, Any]], Any]] = None,
        max_inflight: int = 1,
    ) -> None:
        self._devices[name] = VirtualDevice(name, handler, max_inflight)

    def register_syscall(self, name: str, handler: Callable[[SyscallContext], Any]) -> None:
        self._syscalls[name] = handler

    def invoke_syscall(self, name: str, **kwargs: Any) -> SyscallResult:
        handler = self._syscalls.get(name)
        if handler is None:
            raise KeyError(f"unknown syscall '{name}'")
        ctx = SyscallContext(self)
        try:
            raw_result = handler(ctx, **kwargs)
        except Exception as exc:  # pragma: no cover - best effort
            result = SyscallResult(False, error=str(exc))
        else:
            result = self._normalize_syscall_result(raw_result)
        self._syscall_invocations += 1
        if not result.success:
            self._syscall_denials += 1
        return result

    def register_interrupt_handler(
        self,
        device_name: str,
        handler: Callable[[DeviceInterrupt], None],
    ) -> None:
        self._interrupt_handlers[device_name].append(handler)

    def process_interrupts(self) -> None:
        while self._interrupt_queue:
            interrupt = self._interrupt_queue.pop(0)
            for handler in self._interrupt_handlers.get(interrupt.device_name, []):
                handler(interrupt)

    def complete_device_request(
        self,
        device_name: str,
        ticket: Optional[int],
        *,
        success: bool = True,
        error: Optional[str] = None,
        result: Any = None,
    ) -> None:
        if ticket is None:
            return
        device = self._devices.get(device_name)
        if device is None:
            return
        try:
            interrupt = device.complete(ticket, success=success, error=error, result=result)
        except KeyError:
            return
        self._enqueue_interrupt(interrupt)

    def get_device_metrics(self, device_name: str) -> Optional[Dict[str, Any]]:
        device = self._devices.get(device_name)
        if device is None:
            return None
        return {
            "inflight": device.inflight,
            "capacity": device.max_inflight,
        }

    def _submit_device_request(
        self,
        device_name: str,
        payload: Dict[str, Any],
        *,
        mode: str = "instant",
    ) -> DeviceSubmitResult:
        device = self._devices.get(device_name)
        if device is None:
            return DeviceSubmitResult(accepted=False, reason="unknown-device")
        return device.submit(payload, mode=mode)

    def _enqueue_interrupt(self, interrupt: DeviceInterrupt) -> None:
        self._interrupt_queue.append(interrupt)

    def _normalize_syscall_result(self, raw_value: Any) -> SyscallResult:
        if isinstance(raw_value, SyscallResult):
            return raw_value
        if isinstance(raw_value, bool):
            return SyscallResult(raw_value)
        return SyscallResult(True, result=raw_value)
