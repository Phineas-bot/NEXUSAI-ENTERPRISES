import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import Simulator  # noqa: E402
from storage_virtual_network import StorageVirtualNetwork  # noqa: E402
from storage_virtual_node import StorageVirtualNode, TransferStatus  # noqa: E402

FILE_SIZE = 100 * 1024 * 1024  # 100MB
BANDWIDTH_MBPS = 1000


def _build_network():
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005)

    source = StorageVirtualNode(
        "node-a",
        cpu_capacity=4,
        memory_capacity=16,
        storage_capacity=500,
        bandwidth=BANDWIDTH_MBPS,
    )
    target = StorageVirtualNode(
        "node-b",
        cpu_capacity=8,
        memory_capacity=32,
        storage_capacity=500,
        bandwidth=BANDWIDTH_MBPS,
    )

    network.add_node(source)
    network.add_node(target)
    network.connect_nodes(source.node_id, target.node_id, bandwidth=BANDWIDTH_MBPS)

    return sim, network


def _run_single_transfer(file_size: int = FILE_SIZE):
    sim, network = _build_network()
    transfer = network.initiate_file_transfer("node-a", "node-b", "single.bin", file_size)
    assert transfer is not None
    sim.run()
    return transfer


def _run_parallel_transfers(file_size: int = FILE_SIZE):
    sim, network = _build_network()
    transfer_one = network.initiate_file_transfer("node-a", "node-b", "parallel-1.bin", file_size)
    transfer_two = network.initiate_file_transfer("node-a", "node-b", "parallel-2.bin", file_size)
    assert transfer_one is not None and transfer_two is not None
    sim.run()
    return transfer_one, transfer_two


def test_concurrent_transfers_share_bandwidth():
    baseline = _run_single_transfer()
    assert baseline.status == TransferStatus.COMPLETED
    baseline_duration = baseline.completed_at - baseline.created_at

    transfer_one, transfer_two = _run_parallel_transfers()
    assert transfer_one.status == TransferStatus.COMPLETED
    assert transfer_two.status == TransferStatus.COMPLETED

    duration_one = transfer_one.completed_at - transfer_one.created_at
    duration_two = transfer_two.completed_at - transfer_two.created_at

    # Concurrent transfers should take noticeably longer than a single transfer
    expected_slowdown = baseline_duration * 1.8
    assert max(duration_one, duration_two) > expected_slowdown * 0.85

    # Both transfers should complete within 10% of each other (shared bandwidth fairness)
    max_duration = max(duration_one, duration_two)
    assert abs(duration_one - duration_two) <= max_duration * 0.1

    # Target node should now store both files
    assert transfer_two.total_size + transfer_one.total_size == FILE_SIZE * 2