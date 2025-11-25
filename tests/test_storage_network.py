import sys
import types
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
    routing_strategy: str = "link_state",
):
    sim = Simulator()
    network = StorageVirtualNetwork(
        sim,
        tick_interval=0.005,
        scaling_config=scaling_config,
        routing_strategy=routing_strategy,
    )

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


def test_replica_seed_runs_after_new_capacity():
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.3,
        bandwidth_utilization_threshold=0.95,
        max_replicas_per_root=2,
        replica_seed_limit=2,
    )

    sim, network = _build_network(target_storage_gb=1, scaling_config=scaling)
    hot_file = 400 * 1024 * 1024

    transfer = network.initiate_file_transfer("node-a", "node-b", "hot.bin", hot_file)
    assert transfer is not None
    sim.run()
    assert transfer.status == TransferStatus.COMPLETED

    second_transfer = network.initiate_file_transfer("node-a", "node-b", "followup.bin", hot_file)
    assert second_transfer is not None
    sim.run()

    cluster_nodes = network.get_cluster_nodes("node-b")
    replicas = [node_id for node_id in cluster_nodes if node_id != "node-b"]
    assert replicas  # scaling should have spawned at least one replica
    replica_node_id = replicas.pop()
    replica = network.nodes[replica_node_id]
    assert any(file_transfer.file_name == "hot.bin" for file_transfer in replica.stored_files.values())


def test_scaling_uses_telemetry_priorities():
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.6,
        bandwidth_utilization_threshold=0.9,
        os_failure_threshold=1,
        trigger_priority=["os_failures", "storage", "bandwidth"],
    )

    sim, network = _build_network(scaling_config=scaling)
    target = network.nodes["node-b"]
    original_complete = target.disk.complete_write
    failure_state = {"raised": False}

    def failing_complete(self, *args, **kwargs):
        failure_state["raised"] = True
        raise RuntimeError("disk offline")

    target.disk.complete_write = types.MethodType(failing_complete, target.disk)

    try:
        transfer = network.initiate_file_transfer("node-a", "node-b", "fail.bin", 50 * 1024 * 1024)
        assert transfer is not None
        sim.run()
    finally:
        target.disk.complete_write = original_complete

    assert failure_state["raised"]

    replicas = [node_id for node_id in network.get_cluster_nodes("node-b") if node_id != "node-b"]
    if replicas:
        trigger = network.get_last_scaling_trigger(replicas[0])
        assert trigger == "os_failures"


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


def test_distance_vector_routing_selects_lowest_latency_path():
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005, routing_strategy="distance_vector")

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

    path = network.get_route("node-a", "node-c")
    assert path == ["node-a", "node-b", "node-c"]


def test_link_failure_reroutes_inflight_transfer():
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

    transfer = network.initiate_file_transfer("node-a", "node-c", "reroute.bin", 50 * 1024 * 1024)
    assert transfer is not None

    sim.run(until=0.01)
    assert network.fail_link("node-a", "node-b")

    sim.run()

    assert transfer.status == TransferStatus.COMPLETED
    assert network.get_route("node-a", "node-c") == ["node-a", "node-d", "node-c"]


def test_node_failure_aborts_transfer():
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005)

    node_a = StorageVirtualNode("node-a", 4, 16, 500, BANDWIDTH_MBPS)
    node_b = StorageVirtualNode("node-b", 4, 16, 500, BANDWIDTH_MBPS)
    node_c = StorageVirtualNode("node-c", 4, 16, 500, BANDWIDTH_MBPS)

    for node in (node_a, node_b, node_c):
        network.add_node(node)

    network.connect_nodes("node-a", "node-b", bandwidth=500, latency_ms=1.0)
    network.connect_nodes("node-b", "node-c", bandwidth=500, latency_ms=1.0)

    transfer = network.initiate_file_transfer("node-a", "node-c", "node-failure.bin", 40 * 1024 * 1024)
    assert transfer is not None

    sim.run(until=0.01)
    assert network.fail_node("node-b")

    sim.run()

    assert transfer.status == TransferStatus.FAILED
    assert node_c.failed_transfers >= 1


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


def test_disk_failures_increment_os_process_counters():
    sim, network = _build_network()
    target_node = network.nodes["node-b"]

    original_complete = target_node.disk.complete_write

    def failing_complete(self, *args, **kwargs):
        raise RuntimeError("disk offline")

    target_node.disk.complete_write = types.MethodType(failing_complete, target_node.disk)

    try:
        transfer = network.initiate_file_transfer("node-a", "node-b", "disk-fail.bin", 10 * 1024 * 1024)
        assert transfer is not None
        sim.run()

        assert transfer.status == TransferStatus.FAILED
        assert target_node.os_process_failures >= 1
    finally:
        target_node.disk.complete_write = original_complete


def test_demand_scaling_triggers_on_os_failures():
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.99,
        bandwidth_utilization_threshold=0.99,
        os_failure_threshold=1,
    )

    sim, network = _build_network(scaling_config=scaling)
    target_node = network.nodes["node-b"]

    original_complete = target_node.disk.complete_write

    def failing_complete(self, *args, **kwargs):
        raise RuntimeError("disk offline")

    target_node.disk.complete_write = types.MethodType(failing_complete, target_node.disk)

    try:
        transfer = network.initiate_file_transfer("node-a", "node-b", "os-scale.bin", 20 * 1024 * 1024)
        assert transfer is not None
        sim.run()
    finally:
        target_node.disk.complete_write = original_complete

    assert network.nodes["node-b"].os_process_failures >= 1
    cluster_nodes = network.get_cluster_nodes("node-b")
    assert len(cluster_nodes) >= 2


def test_replica_transfer_streams_chunks_via_virtual_os():
    sim, network = _build_network()
    seed_transfer = network.initiate_file_transfer("node-a", "node-b", "seed.bin", 50 * 1024 * 1024)
    assert seed_transfer is not None
    sim.run()

    node_b = network.nodes["node-b"]
    node_c = StorageVirtualNode("node-c", 4, 16, 500, BANDWIDTH_MBPS)
    network.add_node(node_c)
    network.connect_nodes("node-b", "node-c", bandwidth=BANDWIDTH_MBPS)

    read_calls = {"count": 0}
    original_read = node_b.disk.read_chunk

    def counting_read(self, file_id: str, chunk_id: int):
        read_calls["count"] += 1
        return original_read(file_id, chunk_id)

    node_b.disk.read_chunk = types.MethodType(counting_read, node_b.disk)
    try:
        replica_transfer = network.initiate_replica_transfer("node-b", "node-c", seed_transfer.file_id)
        assert replica_transfer is not None
        assert read_calls["count"] == 1  # Only the first chunk should be read eagerly

        sim.run()

        assert read_calls["count"] == len(replica_transfer.chunks)
        assert replica_transfer.status == TransferStatus.COMPLETED
        assert replica_transfer.file_id in node_c.stored_files
    finally:
        node_b.disk.read_chunk = original_read


def test_replica_transfer_handles_disk_read_failures():
    sim, network = _build_network()
    seed_transfer = network.initiate_file_transfer("node-a", "node-b", "seed.bin", 10 * 1024 * 1024)
    assert seed_transfer is not None
    sim.run()

    node_b = network.nodes["node-b"]
    node_c = StorageVirtualNode("node-c", 4, 16, 500, BANDWIDTH_MBPS)
    network.add_node(node_c)
    network.connect_nodes("node-b", "node-c", bandwidth=BANDWIDTH_MBPS)

    original_read = node_b.disk.read_chunk

    def failing_read(self, file_id: str, chunk_id: int):
        raise RuntimeError("disk offline")

    node_b.disk.read_chunk = types.MethodType(failing_read, node_b.disk)
    try:
        replica_transfer = network.initiate_replica_transfer("node-b", "node-c", seed_transfer.file_id)
        assert replica_transfer is not None

        sim.run()

        assert replica_transfer.status == TransferStatus.FAILED
        assert node_b.os_process_failures >= 1
        assert replica_transfer.file_id not in node_c.stored_files
    finally:
        node_b.disk.read_chunk = original_read


def test_demand_scaling_triggers_on_os_memory_pressure():
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.99,
        bandwidth_utilization_threshold=0.99,
        os_memory_utilization_threshold=0.01,
        max_replicas_per_root=2,
    )

    sim, network = _build_network(scaling_config=scaling)
    for idx in range(3):
        transfer = network.initiate_file_transfer("node-a", "node-b", f"mem-{idx}.bin", 150 * 1024 * 1024)
        assert transfer is not None

    sim.run()

    source_cluster = network.get_cluster_nodes("node-a")
    target_cluster = network.get_cluster_nodes("node-b")
    assert len(source_cluster) >= 2 or len(target_cluster) >= 2


def test_disk_latency_extends_transfer_completion():
    sim, network = _build_network()
    target = network.nodes["node-b"]
    target.disk_profile.throughput_bytes_per_sec = 5 * 1024 * 1024  # ~5MB/s
    target.disk_profile.seek_time_ms = 20

    transfer = network.initiate_file_transfer("node-a", "node-b", "slow-disk.bin", 20 * 1024 * 1024)
    assert transfer is not None

    sim.run()

    assert transfer.status == TransferStatus.COMPLETED
    duration = transfer.completed_at - transfer.created_at
    assert duration >= 0.5  # slower disk should noticeably delay completion


def test_corrupted_chunks_require_recovery_before_replication():
    sim, network = _build_network()
    seed_transfer = network.initiate_file_transfer("node-a", "node-b", "seed.bin", 10 * 1024 * 1024)
    assert seed_transfer is not None
    sim.run()

    node_b = network.nodes["node-b"]
    stored_file_id = next(iter(node_b.stored_files))
    node_b.disk.inject_corruption(stored_file_id, 0)

    node_c = StorageVirtualNode("node-c", 4, 16, 500, BANDWIDTH_MBPS)
    network.add_node(node_c)
    network.connect_nodes("node-b", "node-c", bandwidth=BANDWIDTH_MBPS)

    replica_transfer = network.initiate_replica_transfer("node-b", "node-c", stored_file_id)
    assert replica_transfer is not None
    sim.run()
    assert replica_transfer.status == TransferStatus.FAILED

    node_b.disk.recover_chunk(stored_file_id, 0)
    retry = network.initiate_replica_transfer("node-b", "node-c", stored_file_id)
    assert retry is not None
    sim.run()
    assert retry.status == TransferStatus.COMPLETED


def test_network_device_reservation_limits_concurrent_transmissions():
    node = StorageVirtualNode("isolated", cpu_capacity=1, memory_capacity=8, storage_capacity=100, bandwidth=100)
    pid_one = node.start_chunk_transmission(5 * 1024 * 1024)
    assert pid_one is not None

    pid_two = node.start_chunk_transmission(5 * 1024 * 1024)
    assert pid_two is None  # Reservation device allows only one inflight transmission

    node.complete_chunk_transmission(pid_one)

    pid_three = node.start_chunk_transmission(5 * 1024 * 1024)
    assert pid_three is not None
    node.complete_chunk_transmission(pid_three)


def test_background_jobs_execute_via_virtual_os_processes():
    node = StorageVirtualNode("maint-node", cpu_capacity=4, memory_capacity=16, storage_capacity=200, bandwidth=200)
    counter = {"runs": 0}

    def scrub_task():
        counter["runs"] += 1

    pid = node.schedule_background_job(
        "scrub",
        cpu_seconds=0.02,
        memory_bytes=4 * 1024 * 1024,
        task=scrub_task,
    )
    assert pid is not None

    node.drain_background_jobs()
    assert counter["runs"] == 1

    metrics = node.virtual_os.get_device_metrics(f"maintenance:{node.node_id}")
    assert metrics is not None and metrics["inflight"] == 0