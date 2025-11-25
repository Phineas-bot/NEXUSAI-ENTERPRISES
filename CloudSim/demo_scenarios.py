from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, List, Optional

from simulator import Simulator
from storage_virtual_network import DemandScalingConfig, StorageVirtualNetwork
from storage_virtual_node import StorageVirtualNode, TransferStatus

MB = 1024 * 1024


class DemoResult(dict):
    """Typed dict wrapper for scenario summaries."""


def _make_event_logger(event_log: List[str], limit: int) -> callable:
    def _logger(event: Dict[str, object]) -> None:
        if len(event_log) >= limit:
            return
        event_type = event.get("type", "unknown")
        timestamp = event.get("time", 0.0)
        details = {k: v for k, v in event.items() if k not in {"type", "time"}}
        event_log.append(f"[{timestamp:0.2f}s] {event_type} {details}")

    return _logger


def _build_node(node_id: str, storage_gb: int = 500, bandwidth_mbps: int = 1000) -> StorageVirtualNode:
    return StorageVirtualNode(
        node_id,
        cpu_capacity=8,
        memory_capacity=32,
        storage_capacity=storage_gb,
        bandwidth=bandwidth_mbps,
    )


def _transfer_stats(transfers: Iterable[Optional[object]]) -> List[Dict[str, object]]:
    stats = []
    for transfer in transfers:
        if not transfer:
            continue
        duration = None
        if transfer.created_at is not None and transfer.completed_at is not None:
            duration = transfer.completed_at - transfer.created_at
        stats.append(
            {
                "file": transfer.file_name,
                "size_bytes": transfer.total_size,
                "status": transfer.status.name,
                "duration": duration,
                "chunks": len(transfer.chunks),
            }
        )
    return stats


def _snapshot_cluster(network: StorageVirtualNetwork, root_id: str) -> Dict[str, object]:
    cluster = sorted(network.get_cluster_nodes(root_id))
    replicas = [node_id for node_id in cluster if node_id != root_id]
    telemetry: Dict[str, Optional[Dict[str, object]]] = {}
    for node_id in cluster:
        telem = network.get_node_telemetry(node_id)
        telemetry[node_id] = asdict(telem) if telem else None
    last_triggers = {node_id: network.get_last_scaling_trigger(node_id) for node_id in cluster}
    replica_parents = {
        node_id: network.get_replica_parent(node_id)
        for node_id in replicas
    }
    return {
        "root": root_id,
        "cluster": cluster,
        "replicas": replicas,
        "replica_parents": replica_parents,
        "last_triggers": last_triggers,
        "telemetry": telemetry,
    }


def run_hotspot_scaling_demo(event_limit: int = 25) -> DemoResult:
    sim = Simulator()
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.35,
        bandwidth_utilization_threshold=0.95,
        max_replicas_per_root=2,
        replica_seed_limit=2,
    )
    network = StorageVirtualNetwork(sim, tick_interval=0.005, scaling_config=scaling)
    events: List[str] = []
    network.register_observer(_make_event_logger(events, event_limit))

    source = _build_node("node-a", storage_gb=500)
    target = _build_node("node-b", storage_gb=1)
    network.add_node(source)
    network.add_node(target)
    network.connect_nodes("node-a", "node-b", bandwidth=1000)

    hot_file = 400 * MB
    first = network.initiate_file_transfer("node-a", "node-b", "hot.bin", hot_file)
    sim.run()
    second = network.initiate_file_transfer("node-a", "node-b", "followup.bin", hot_file)
    sim.run()

    summary = DemoResult(
        scenario="hotspot-scaling",
        events=events,
        transfers=_transfer_stats([first, second]),
        cluster=_snapshot_cluster(network, "node-b"),
    )
    return summary


def run_disk_failure_demo(event_limit: int = 25) -> DemoResult:
    sim = Simulator()
    scaling = DemandScalingConfig(
        enabled=True,
        storage_utilization_threshold=0.99,
        bandwidth_utilization_threshold=0.9,
        os_failure_threshold=1,
        trigger_priority=["os_failures", "storage", "bandwidth"],
    )
    network = StorageVirtualNetwork(sim, tick_interval=0.005, scaling_config=scaling)
    events: List[str] = []
    network.register_observer(_make_event_logger(events, event_limit))

    source = _build_node("node-a")
    target = _build_node("node-b")
    network.add_node(source)
    network.add_node(target)
    network.connect_nodes("node-a", "node-b", bandwidth=1000)

    original_complete = target.disk.complete_write

    def failing_complete(self, *args, **kwargs):
        raise RuntimeError("disk offline")

    target.disk.complete_write = failing_complete.__get__(target.disk, type(target.disk))

    try:
        transfer = network.initiate_file_transfer("node-a", "node-b", "fail.bin", 50 * MB)
        sim.run()
    finally:
        target.disk.complete_write = original_complete

    summary = DemoResult(
        scenario="disk-failure",
        events=events,
        transfers=_transfer_stats([transfer]),
        cluster=_snapshot_cluster(network, "node-b"),
        metrics={"os_failures": target.os_process_failures},
    )
    return summary


def run_routing_failover_demo(event_limit: int = 25) -> DemoResult:
    sim = Simulator()
    network = StorageVirtualNetwork(sim, tick_interval=0.005)
    events: List[str] = []
    network.register_observer(_make_event_logger(events, event_limit))

    node_a = _build_node("node-a")
    node_b = _build_node("node-b")
    node_c = _build_node("node-c")
    node_d = _build_node("node-d")

    for node in (node_a, node_b, node_c, node_d):
        network.add_node(node)

    network.connect_nodes("node-a", "node-b", bandwidth=500, latency_ms=1.0)
    network.connect_nodes("node-b", "node-c", bandwidth=500, latency_ms=1.0)
    network.connect_nodes("node-a", "node-d", bandwidth=500, latency_ms=5.0)
    network.connect_nodes("node-d", "node-c", bandwidth=500, latency_ms=5.0)

    transfer = network.initiate_file_transfer("node-a", "node-c", "route.bin", 50 * MB)
    sim.run()
    first_route = network.get_route("node-a", "node-c")

    network.fail_link("node-a", "node-b")
    retry = network.initiate_file_transfer("node-a", "node-c", "reroute.bin", 50 * MB)
    sim.run()
    second_route = network.get_route("node-a", "node-c")

    summary = DemoResult(
        scenario="routing-failover",
        events=events,
        transfers=_transfer_stats([transfer, retry]),
        routes={"initial": first_route, "after_failure": second_route},
    )
    return summary


SCENARIOS = {
    "hotspot": run_hotspot_scaling_demo,
    "failure": run_disk_failure_demo,
    "routing": run_routing_failover_demo,
}


def run_scenario(name: str, *, event_limit: int = 25) -> DemoResult:
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{name}'")
    return SCENARIOS[name](event_limit=event_limit)


def run_all_scenarios(event_limit: int = 25) -> List[DemoResult]:
    return [runner(event_limit=event_limit) for runner in SCENARIOS.values()]
