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
- **Replica Transfers**: `StorageVirtualNetwork.initiate_replica_transfer` reuses stored data, driving disk reads through each node's VirtualOS so replicas receive consistent chunks without pre-reading entire files.

## Running the Test Suite

Use the existing virtual environment and execute the Pytest suite to validate the simulator and bandwidth logic:

```powershell
cd "C:/Users/USER PRO/nexusAI/NEXUSAI-ENTERPRISES"
.venv\Scripts\python.exe -m pytest
```

The suite currently includes:

- `tests/test_simulator.py`: Ensures the event scheduler respects absolute times and priority ordering.
- `tests/test_storage_network.py`: Covers bandwidth fairness, decentralized demand scaling, multi-hop routing, VirtualOS-driven backpressure, and the new failover+distance-vector routing scenarios.
- `tests/test_virtual_disk.py`: Validates disk reservations, chunk commits, and capacity reclamation logic.
- `tests/test_virtual_os.py`: Exercises the scheduler directly (process completion, memory pressure, and block/unblock cycles).
- `tests/test_demo_scenarios.py`: Smoke-tests the curated demo scenarios and their telemetry outputs.
- `tests/test_controller.py`: Covers the interactive controller helpers (node creation/listing and size parsing utilities).

## Interactive Control Shell

Run the (default) interactive shell to create nodes, connect links, start transfers, and inspect cluster state without editing code:

```powershell
.venv\Scripts\python.exe CloudSim/main.py --mode interactive
```

Example session commands:

```text
cloudsim> add node-a --storage 500 --bandwidth 1500
cloudsim> add node-b --storage 200
cloudsim> connect node-a node-b --bandwidth 500
cloudsim> transfer node-a node-b demo.bin 200MB
cloudsim> nodes
cloudsim> events 5
```

The shell keeps the simulator alive so you can mark nodes offline (`fail node-b`), restore them, disconnect links, or inspect replica clusters in real time. Use `help` or `help <command>` from inside the shell to see the full catalog of actions.

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
- Disk writes now execute inside VirtualOS-managed processes, so storage failures surface as OS process errors and consume CPU/RAM budgets just like network activity.
- Disk retrievals also run through the VirtualOS layer; attempts to read from offline disks or exhausted memory pools fail fast before a transfer is assembled.
- Asynchronous I/O scheduling simulates seek + throughput costs per operation; every chunk write obtains a `DiskIOTicket`, and the network only marks a chunk complete once the disk commit event fires at its scheduled simulator timestamp.
- `VirtualDisk` tracks optional filesystem metadata via `reserve_file(..., path="/node/file.bin")` and can list directories or persist bytes to a host folder when `persist_root` is configured.
- Integrity hooks expose `inject_corruption`, checksum verification on reads, and `recover_chunk` helpers so tests can model bitrot and recovery workflows before replica transfers proceed.

## Routing & IP Simulation

- `StorageVirtualNetwork` automatically assigns `10.0.x.y` addresses to nodes and tracks per-link latency and bandwidth metrics.
- A link-state shortest-path algorithm (Dijkstra) or optional distance-vector strategy computes end-to-end routes; transfers that lack a route fail fast, and tests can introspect the chosen path via `StorageVirtualNetwork.get_route`.
- Each chunk traverses every hop in its route, sharing bandwidth per link; excess capacity on one hop immediately advances the chunk to the next hop, so pipelines form naturally.
- Latency metrics propagate when replica nodes spawn, ensuring new links integrate seamlessly with the routing fabric.

## Network Failover & Advanced Routing

- `StorageVirtualNetwork` now accepts a `routing_strategy` of `"link-state"` (default) or `"distance-vector"`, letting scenarios bias toward global optimality or hop-by-hop convergence without rewriting tests.
- Links and nodes expose `fail_link`, `restore_link`, `fail_node`, and `restore_node` helpers so simulations can trigger outages; the network automatically removes failed resources from routing tables.
- In-flight transfers detect mid-route failures, attempt to recompute a viable path, and either resume on the new route or surface deterministic errors if capacity is exhausted or isolation persists.
- Distance-vector routing uses periodic neighbor updates with latency-aware weights, producing different but predictable paths that the new regression suite asserts explicitly.
- See `tests/test_storage_network.py::test_link_failure_reroutes_inflight_transfer`, `...::test_node_failure_aborts_transfer`, and `...::test_distance_vector_routing_selects_lowest_latency_path` for concrete failover and strategy coverage.

## Virtual OS Resource Model

- Each chunk ingestion first spawns a short-lived process inside the node’s `VirtualOS`; if CPU or RAM are exhausted the transfer aborts early and releases disk reservations.
- Network egress also flows through `VirtualOS.start_chunk_transmission`, so oversubscribed senders fail fast rather than silently queueing unlimited traffic. See `tests/test_storage_network.py::test_virtual_os_backpressure_limits_parallel_transmissions` for coverage.
- Nodes surface OS health in `get_performance_metrics()` (used memory + failure counters) so scaling policies can consider compute pressure alongside storage and bandwidth.
- Disk operations run as VirtualOS processes as well; when `VirtualDisk.write_chunk` raises, the owning node increments `os_process_failures`, and transfers fail deterministically (`tests/test_storage_network.py::test_disk_failures_increment_os_process_counters`).
- Demand scaling now inspects OS pressure: configure `os_failure_threshold` (spawns replicas after N recent process failures) and `os_memory_utilization_threshold` (replicates when RAM pressure stays high) in `DemandScalingConfig` to keep hot nodes from thrashing their virtual kernels.
- Every controller-managed node automatically spawns two replicas. Transfers immediately seed those replicas, and the network mirrors new links to each replica so failing the parent node still leaves the replicated data reachable. Inspect redundancy via the `clusters` shell command.
- Use `StorageVirtualNetwork.initiate_replica_transfer(owner_node_id, target_node_id, file_id)` to clone stored data across the network. Each chunk read is scheduled via `prepare_chunk_read`, so replication respects CPU/RAM budgets and bubbles up disk read failures just like ingestion (`tests/test_storage_network.py::test_replica_transfer_streams_chunks_via_virtual_os`).
- The VirtualOS now exposes explicit syscalls (`disk_read`, `disk_write`, `network_send`, `maintenance_hook`) backed by device abstractions so disks, NICs, and maintenance hooks enforce centralized backpressure instead of ad-hoc driver calls.
- Device reservations emit interrupts when work finishes; tests such as `tests/test_virtual_os.py::test_syscalls_route_through_devices_and_interrupts` and `...::test_reservation_devices_enforce_backpressure` cover the behavior.
- Background maintenance (replication, scrubbing) can be scheduled through `StorageVirtualNode.schedule_background_job`, which spins VirtualOS processes and uses the maintenance syscall so only one heavy job runs per node at a time (`tests/test_storage_network.py::test_background_jobs_execute_via_virtual_os_processes`).
