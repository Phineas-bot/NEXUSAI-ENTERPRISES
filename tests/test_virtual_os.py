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