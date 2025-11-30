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
- **Control Plane gRPC API**: `cloud_drive/api/protos/control_plane.proto` defines Files, Uploads, Sharing, and Operations services plus shared messages (pagination, LRO metadata, ACL context). Use `python -m grpc_tools.protoc -I cloud_drive/api/protos --python_out=. --grpc_python_out=. cloud_drive/api/protos/control_plane.proto` to generate Python stubs for new handlers.

## Running the Test Suite

Use the existing virtual environment and execute the Pytest suite to validate the simulator and bandwidth logic:

```powershell
cd "C:/Users/USER PRO/nexusAI/NEXUSAI-ENTERPRISES"
.venv\Scripts\python.exe -m pytest
```

## gRPC Control Plane

- Install the runtime/tooling dependencies inside the venv:
	```powershell
	.venv\Scripts\python.exe -m pip install grpcio grpcio-tools
	```
- (Re)generate Python bindings whenever `control_plane.proto` changes:
	```powershell
	.venv\Scripts\python.exe -m grpc_tools.protoc -I cloud_drive/api/protos --python_out=cloud_drive/api --grpc_python_out=cloud_drive/api cloud_drive/api/protos/control_plane.proto
	```
- Launch the gRPC server (insecure development port by default):
	```powershell
	.venv\Scripts\python.exe -m cloud_drive.api.grpc_server --bind localhost:50051
	```
	The server wires Files/Uploads/Sharing/Operations services directly into the running simulator; see `tests/test_cloud_drive_grpc.py` for an end-to-end example client.
	Uploads now persist manifests into the storage fabric during `Finalize`, so the `UploadsService.DownloadChunks` streaming RPC can read those manifests back and emit chunked responses (the test covers both full and partial reads).
- Provide TLS credentials to the standalone gRPC process with `--tls-cert path/to/cert.pem --tls-key path/to/key.pem`. The FastAPI gateway picks up the same capability via environment variables: set `CLOUD_DRIVE_GRPC_TLS_CERT` and `CLOUD_DRIVE_GRPC_TLS_KEY` before launching `uvicorn` to expose the embedded listener over TLS instead of plaintext.
- When you run the FastAPI gateway (for example `uvicorn cloud_drive.api.server:app --reload`), the same module now boots the gRPC control-plane server in-process using the shared runtime. Override the bind address with `CLOUD_DRIVE_GRPC_BIND=127.0.0.1:55051` if the default `0.0.0.0:50051` conflicts with other services.
- The FastAPI server now mirrors the gRPC finalize/download flow: `POST /uploads:finalize/{session}` returns an LRO-style payload containing the resolved file + manifest IDs, and `GET /files/{id}/download` streams file contents (with optional `offset`, `length`, and `chunk_size` query params) by delegating to the same manifest-driven pipeline.

## Background Jobs & Scheduling

- `CloudDriveRuntime.run_background_jobs()` is the single entry point that fans out to all policy-driven maintenance loops. A single invocation performs two units of work:
	- `LifecycleManager.evaluate_transitions()` demotes cold data and emits `lifecycle.transitions` bus events, but it short-circuits unless the configured `rebalance_interval_seconds` window has elapsed. Runners should therefore schedule the background job no less frequently than that interval (default: 1 hour) to keep hot/cold annotations current.
	- `HealingService.run_health_checks()` batches reconciliation, checksum scrubbing, degraded-node evacuation, and orphan cleanup. The method publishes a consolidated `healing.events` payload whenever any of those lists are non-empty so operators can hook alerting or telemetry.
- Neither the FastAPI nor gRPC servers trigger these jobs automatically; production deployments must wire a scheduler. Common patterns include:
	1. **Process-local loop** – when embedding the API inside a long-running process, start an asyncio/background task during `startup` that sleeps for `N` seconds (for example 60–300) between calls to `runtime.run_background_jobs()`. Keep `N` aligned with the lifecycle interval and desired healing cadence.
	2. **External orchestrator** – in batch or multi-process setups, point cron/Task Scheduler/Kubernetes CronJobs at a short Python script that imports `CloudDriveRuntime`, calls `bootstrap()`, invokes `run_background_jobs()`, and exits. This keeps the maintenance plane decoupled from the API surface.
	3. **Event-driven hook** – after simulating failures or injecting new manifests in tests/demos, explicitly call `runtime.run_background_jobs()` once so rebalance/healing effects are observable immediately.
- As a rule of thumb, run the job:
	- Shortly after upload bursts (to enforce replica/durability policy before the next client read).
	- Within one rebalance interval after a node failure or capacity alert so evacuation kicks in.
	- At least 4× per day even in idle clusters to ensure checksum scrubbing and orphan cleanup stay current when `DurabilityPolicyConfig.enable_scrubbing` is true.
- Instrumentation teams can watch `healing.events` and `lifecycle.transitions` topics (via the configured message bus backend) to confirm the scheduler is firing; lack of events over multiple intervals usually indicates the job runner has stalled.

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
cloudsim> connect node-a node-b node-c --bandwidth 500
cloudsim> transfer node-a node-b demo.bin 200MB
cloudsim> inspect node-b
cloudsim> nodes --all
cloudsim> events 5
cloudsim> save backups/lab1.json
cloudsim> load backups/lab1.json
cloudsim> reset --clear
```

The shell keeps the simulator alive so you can mark nodes offline (`fail node-b`), restore them, disconnect links, or inspect replica clusters in real time. Use `help` or `help <command>` from inside the shell to see the full catalog of actions.
`connect` accepts more than two node IDs and links each adjacent pair, and `inspect NODE_ID` dumps bandwidth/storage telemetry, stored files, replica parents/children, and active transfers.
`save [PATH]` writes a JSON snapshot (defaulting to `CloudSim/cloudsim_state.json`), `load [PATH]` restores a snapshot and switches the autosave target, and `reset [--clear]` wipes the in-memory topology (optionally deleting the saved snapshot).

### Persistent Sessions

- The interactive shell now auto-loads and auto-saves `CloudSim/cloudsim_state.json`, so any nodes, links, stored files, and event history remain intact after you close and reopen the terminal.
- State snapshots capture cluster topology, replica membership, node telemetry, and on-disk file metadata; successful transfers are available immediately after restarting.
- Programmatic callers can opt into the same behavior by instantiating `CloudSimController(enable_persistence=True, state_path="/path/to/state.json")`.

## Bandwidth-Sharing Expectations

- When a link has multiple active transfers, each transfer receives an equal share of the link bandwidth during every tick.
- Transfers that cannot obtain bandwidth fail fast rather than stalling indefinitely.
- Regression tests assert both the slowdown (relative to single-transfer runs) and fairness between concurrent transfers. Adjust the tick interval or tolerances if you change the sharing algorithm.

## Demand-Driven Scaling

- Each node belongs to a replica cluster; any member that approaches configurable storage/bandwidth thresholds can clone itself, inheriting connections from its parent and creating a new path for traffic.
- Replica creation is entirely event-driven—no global controller exists—so saturation on one link or node results in local growth instead of centralized orchestration.
- `DemandScalingConfig` lets you tune utilization thresholds, replica limits, and resource multipliers; see `tests/test_storage_network.py::test_demand_scaling_spawns_replicas_for_hot_targets` for an example harness.

## Replica Consistency Fan-out

- Every successful transfer now seeds the entire cluster for the destination node: the parent (root) stores the dataset, and all healthy replicas immediately receive scheduled replica transfers.
- Uploading directly to a replica backfills the parent and the sibling replicas using `initiate_replica_transfer`, so clients can write to any cluster member without thinking about data placement.
- Replica fan-out uses the same VirtualOS-guarded transfer path as external uploads, ensuring disk bandwidth, CPU, and memory limits are honored during synchronization. Failures raise `replica_sync_failed` events so tests can assert retry behavior.
- Regression coverage lives in `tests/test_storage_network.py::test_completed_transfer_fanouts_to_cluster_replicas` and `::test_transfer_to_replica_backfills_parent_and_siblings`.

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
