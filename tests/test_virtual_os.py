import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from virtual_os import ProcessState, VirtualOS  # noqa: E402


def drain_scheduler(os: VirtualOS, max_ticks: int = 1000):
    for _ in range(max_ticks):
        if not os.has_runnable_work():
            break
        os.schedule_tick()


def test_processes_complete_with_sufficient_resources():
    os = VirtualOS(cpu_capacity=4, memory_capacity_bytes=128 * 1024 * 1024)
    pid_one = os.spawn_process("task-a", cpu_required=0.02, memory_required=8 * 1024 * 1024, target=lambda: None)
    pid_two = os.spawn_process("task-b", cpu_required=0.03, memory_required=16 * 1024 * 1024, target=lambda: None)

    assert pid_one and pid_two

    drain_scheduler(os)

    assert os.get_process(pid_one).state == ProcessState.COMPLETED
    assert os.get_process(pid_two).state == ProcessState.COMPLETED
    assert os.used_memory == 0


def test_memory_pressure_blocks_new_processes():
    os = VirtualOS(cpu_capacity=2, memory_capacity_bytes=16 * 1024 * 1024)
    pid = os.spawn_process("resident", cpu_required=0.05, memory_required=12 * 1024 * 1024, target=lambda: None)
    assert pid is not None

    denied_pid = os.spawn_process("denied", cpu_required=0.01, memory_required=8 * 1024 * 1024, target=lambda: None)
    assert denied_pid is None

    drain_scheduler(os)
    assert os.get_process(pid).state == ProcessState.COMPLETED
    assert os.used_memory == 0


def test_block_and_unblock_cycle():
    os = VirtualOS(cpu_capacity=1, memory_capacity_bytes=32 * 1024 * 1024)
    pid = os.spawn_process("io-bound", cpu_required=0.05, memory_required=4 * 1024 * 1024, target=lambda: None)
    assert pid is not None

    os.block_process(pid)
    # Scheduler should not run blocked processes
    for _ in range(10):
        os.schedule_tick()
    assert os.get_process(pid).state == ProcessState.BLOCKED

    os.unblock_process(pid)
    drain_scheduler(os)
    assert os.get_process(pid).state == ProcessState.COMPLETED


def test_process_target_runs_only_once():
    os = VirtualOS(cpu_capacity=1, memory_capacity_bytes=32 * 1024 * 1024, cpu_time_slice=0.01)
    call_counter = {"count": 0}

    def work():
        call_counter["count"] += 1

    pid = os.spawn_process("disk-io", cpu_required=0.05, memory_required=4 * 1024 * 1024, target=work)
    assert pid is not None

    drain_scheduler(os)

    assert os.get_process(pid).state == ProcessState.COMPLETED
    assert call_counter["count"] == 1


def test_syscalls_route_through_devices_and_interrupts():
    os = VirtualOS(cpu_capacity=1, memory_capacity_bytes=32 * 1024 * 1024)
    observed = {"writes": 0, "interrupts": 0}

    def disk_handler(payload):
        observed["writes"] += 1
        assert payload["file_id"] == "file-a"
        return payload["size"]

    os.register_device("disk:node", handler=disk_handler, max_inflight=1)

    def on_interrupt(event):
        observed["interrupts"] += 1
        assert event.device_name == "disk:node"

    os.register_interrupt_handler("disk:node", on_interrupt)

    def syscall(ctx, *, file_id: str, chunk_id: int, size: int):
        return ctx.device_call(
            "disk:node",
            {
                "op": "write",
                "file_id": file_id,
                "chunk_id": chunk_id,
                "size": size,
            },
        )

    os.register_syscall("disk_write", syscall)
    result = os.invoke_syscall("disk_write", file_id="file-a", chunk_id=1, size=4096)
    assert result.success
    assert observed["writes"] == 1
    os.process_interrupts()
    assert observed["interrupts"] == 1


def test_reservation_devices_enforce_backpressure():
    os = VirtualOS(cpu_capacity=1, memory_capacity_bytes=32 * 1024 * 1024)
    os.register_device("nic:node", handler=None, max_inflight=1)

    def reserve_nic(ctx, *, bytes: int):
        return ctx.device_call(
            "nic:node",
            {"bytes": bytes},
            mode="reservation",
        )

    os.register_syscall("network_send", reserve_nic)

    first = os.invoke_syscall("network_send", bytes=1024)
    assert first.success

    second = os.invoke_syscall("network_send", bytes=512)
    assert not second.success
    assert "ticket" not in second.metadata

    os.complete_device_request("nic:node", first.metadata.get("ticket"))

    third = os.invoke_syscall("network_send", bytes=512)
    assert third.success