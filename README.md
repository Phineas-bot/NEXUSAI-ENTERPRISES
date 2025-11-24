# NEXUSAI CloudSim

A lightweight simulator that models storage nodes, their network interconnects, and a discrete-event engine for testing bandwidth-aware transfers.

## Key Components

- **Simulator**: Drives discrete events and enforces execution order based on absolute time.
- **StorageVirtualNetwork**: Shares link bandwidth across concurrent transfers using a configurable tick interval so chunks progress fairly.
- **StorageVirtualNode**: Captures compute, memory, storage, and link characteristics for each simulated endpoint.
- **VirtualOS**: Lightweight process scheduler embedded in every node that enforces per-chunk CPU and memory reservations before disk or network work can proceed.
- **VirtualDisk**: Provides block-level storage reservations, chunk persistence, and capacity accounting so nodes manage real data instead of counters.
- **Routing Engine**: Assigns IPs automatically, builds per-node routing tables via a latency-aware link-state algorithm, and drives multi-hop chunk forwarding with per-hop bandwidth sharing.
- **Demand Scaling**: A decentralized policy that lets any saturated node spawn replicas and extend the topology without a central coordinator.

## Running the Test Suite

Use the existing virtual environment and execute the Pytest suite to validate the simulator and bandwidth logic:

```powershell
cd "C:/Users/USER PRO/nexusAI/NEXUSAI-ENTERPRISES"
.venv\Scripts\python.exe -m pytest
```

The suite currently includes:

- `tests/test_simulator.py`: Ensures the event scheduler respects absolute times and priority ordering.
- `tests/test_storage_network.py`: Covers bandwidth fairness, decentralized demand scaling, multi-hop routing, and VirtualOS-driven backpressure for oversubscribed senders.
- `tests/test_virtual_disk.py`: Validates disk reservations, chunk commits, and capacity reclamation logic.
- `tests/test_virtual_os.py`: Exercises the scheduler directly (process completion, memory pressure, and block/unblock cycles).

## Bandwidth-Sharing Expectations

- When a link has multiple active transfers, each transfer receives an equal share of the link bandwidth during every tick.
- Transfers that cannot obtain bandwidth fail fast rather than stalling indefinitely.
- Regression tests assert both the slowdown (relative to single-transfer runs) and fairness between concurrent transfers. Adjust the tick interval or tolerances if you change the sharing algorithm.

## Demand-Driven Scaling

- Each node belongs to a replica cluster; any member that approaches configurable storage/bandwidth thresholds can clone itself, inheriting connections from its parent and creating a new path for traffic.
- Replica creation is entirely event-driven—no global controller exists—so saturation on one link or node results in local growth instead of centralized orchestration.
- `DemandScalingConfig` lets you tune utilization thresholds, replica limits, and resource multipliers; see `tests/test_storage_network.py::test_demand_scaling_spawns_replicas_for_hot_targets` for an example harness.

## Disk-Backed Storage

- Every `StorageVirtualNode` mounts a `VirtualDisk` so transfers reserve space before they begin and persist chunk data as it arrives.
- Disk usage metrics distinguish committed bytes from reserved-but-not-yet-written space, preventing overcommit and enabling smarter placement decisions.
- Aborted transfers automatically release their reservations, while successful transfers retain their on-disk chunks for later retrieval or replication.

## Routing & IP Simulation

- `StorageVirtualNetwork` automatically assigns `10.0.x.y` addresses to nodes and tracks per-link latency and bandwidth metrics.
- A link-state shortest-path algorithm (Dijkstra) computes end-to-end routes; transfers that lack a route fail fast, and tests can introspect the chosen path via `StorageVirtualNetwork.get_route`.
- Each chunk traverses every hop in its route, sharing bandwidth per link; excess capacity on one hop immediately advances the chunk to the next hop, so pipelines form naturally.
- Latency metrics propagate when replica nodes spawn, ensuring new links integrate seamlessly with the routing fabric.

## Virtual OS Resource Model

- Each chunk ingestion first spawns a short-lived process inside the node’s `VirtualOS`; if CPU or RAM are exhausted the transfer aborts early and releases disk reservations.
- Network egress also flows through `VirtualOS.start_chunk_transmission`, so oversubscribed senders fail fast rather than silently queueing unlimited traffic. See `tests/test_storage_network.py::test_virtual_os_backpressure_limits_parallel_transmissions` for coverage.
- Nodes surface OS health in `get_performance_metrics()` (used memory + failure counters) so scaling policies can consider compute pressure alongside storage and bandwidth.
