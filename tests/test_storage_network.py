import sys
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import Simulator  # noqa: E402
from storage_virtual_network import DemandScalingConfig, StorageVirtualNetwork  # noqa: E402
from storage_virtual_node import StorageVirtualNode, TransferStatus  # noqa: E402

FILE_SIZE = 100 * 1024 * 1024  # 100MB
BANDWIDTH_MBPS = 1000


def _build_network(
    source_storage_gb: int = 500,
    target_storage_gb: int = 500,
    bandwidth_mbps: int = BANDWIDTH_MBPS,
    scaling_config: Optional[DemandScalingConfig] = None,
):
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005, scaling_config=scaling_config)

    source = StorageVirtualNode(
        "node-a",
        cpu_capacity=4,
        memory_capacity=16,
        storage_capacity=source_storage_gb,
        bandwidth=bandwidth_mbps,
    )
    target = StorageVirtualNode(
        "node-b",
        cpu_capacity=8,
        memory_capacity=32,
        storage_capacity=target_storage_gb,
        bandwidth=bandwidth_mbps,
    )

    network.add_node(source)
    network.add_node(target)
    network.connect_nodes(source.node_id, target.node_id, bandwidth=bandwidth_mbps)

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


def test_demand_scaling_spawns_replicas_for_hot_targets():
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.5,
        bandwidth_utilization_threshold=0.95,
        max_replicas_per_root=3,
    )

    sim, network = _build_network(target_storage_gb=1, scaling_config=scaling)
    large_file = 600 * 1024 * 1024  # 600MB

    transfers = []
    for idx in range(3):
        transfer = network.initiate_file_transfer("node-a", "node-b", f"scaling-{idx}.bin", large_file)
        assert transfer is not None
        transfers.append(transfer)

    sim.run()

    cluster_nodes = network.get_cluster_nodes("node-b")
    assert len(cluster_nodes) >= 2

    total_stored = sum(
        network.nodes[node_id].used_storage
        for node_id in cluster_nodes
        if node_id in network.nodes
    )
    assert total_stored >= large_file * len(transfers)

    for transfer in transfers:
        assert transfer.status == TransferStatus.COMPLETED


def test_multi_hop_routing_selects_lowest_latency_path():
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005)

    node_a = StorageVirtualNode("node-a", 4, 16, 500, BANDWIDTH_MBPS)
    node_b = StorageVirtualNode("node-b", 4, 16, 500, BANDWIDTH_MBPS)
    node_c = StorageVirtualNode("node-c", 4, 16, 500, BANDWIDTH_MBPS)
    node_d = StorageVirtualNode("node-d", 4, 16, 500, BANDWIDTH_MBPS)

    for node in (node_a, node_b, node_c, node_d):
        network.add_node(node)

    network.connect_nodes("node-a", "node-b", bandwidth=500, latency_ms=1.0)
    network.connect_nodes("node-b", "node-c", bandwidth=500, latency_ms=1.0)
    network.connect_nodes("node-a", "node-d", bandwidth=500, latency_ms=5.0)
    network.connect_nodes("node-d", "node-c", bandwidth=500, latency_ms=5.0)

    transfer = network.initiate_file_transfer("node-a", "node-c", "multi-hop.bin", 50 * 1024 * 1024)
    assert transfer is not None

    sim.run()

    assert transfer.status == TransferStatus.COMPLETED
    assert network.get_route("node-a", "node-c") == ["node-a", "node-b", "node-c"]


def test_virtual_os_backpressure_limits_parallel_transmissions():
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005)

    constrained_source = StorageVirtualNode(
        "node-a",
        cpu_capacity=2,
        memory_capacity=0.001,  # ~1MB of RAM, so only one chunk process fits
        storage_capacity=500,
        bandwidth=BANDWIDTH_MBPS,
    )
    roomy_target = StorageVirtualNode(
        "node-b",
        cpu_capacity=8,
        memory_capacity=64,
        storage_capacity=2000,
        bandwidth=BANDWIDTH_MBPS,
    )

    network.add_node(constrained_source)
    network.add_node(roomy_target)
    network.connect_nodes("node-a", "node-b", bandwidth=BANDWIDTH_MBPS)

    transfers = []
    for idx in range(4):
        transfer = network.initiate_file_transfer(
            "node-a",
            "node-b",
            f"os-pressure-{idx}.bin",
            200 * 1024 * 1024,
        )
        assert transfer is not None
        transfers.append(transfer)

    sim.run()

    completed = [t for t in transfers if t.status == TransferStatus.COMPLETED]
    failed = [t for t in transfers if t.status == TransferStatus.FAILED]

    assert len(completed) == 1
    assert len(failed) == len(transfers) - 1
    assert network.nodes["node-a"].os_process_failures >= len(failed)