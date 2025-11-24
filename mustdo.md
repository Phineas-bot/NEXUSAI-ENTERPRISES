# NEXUS Must-Do Design Notes

The following sections outline how we can grow CloudSim into a more realistic distributed system while staying within the simulator’s architecture. Each subsection summarizes scope, key components, integration touchpoints, and incremental milestones so we can stage delivery.

## 1. Real Network with Routing & IP Addressing

**Goal**: move from single-hop transfers to a multi-hop network fabric that assigns IP addresses, routes traffic through intermediate nodes, and shares bandwidth per hop.

### Network Key Design Elements

- **Addressing**: extend `StorageVirtualNode` with `ip_address` (CIDR blocks per cluster). Provide an allocator that can hand out addresses as nodes/replicas spawn.
- **Link State Model**: store per-link latency, bandwidth, and failure state. Represent this as an adjacency list in `StorageVirtualNetwork` for fast updates.
- **Routing Protocol**: start with a link-state algorithm (Dijkstra). Maintain per-node routing tables that recompute whenever topology changes (new replica, link failure, etc.). Later, optionally add distance-vector mode for stress tests.
- **Packet/Chunk Pathing**: break each file chunk transfer into per-hop legs. Bandwidth/fairness logic already works per link; we'll queue each chunk hop separately so congestion is localized.
- **Fault Modeling**: add event hooks so a link/node failure triggers routing recomputation and in-flight chunk rerouting.

### Network Milestones

1. Add IP allocator + adjacency tracking.
2. Implement link-state routing tables and tests that confirm multi-hop shortest paths.
3. Integrate routing with chunk scheduling; ensure per-hop bandwidth accounting works.
4. Add failure injection/regression tests for rerouting.

## 2. Lightweight Virtual OS per Node

**Goal**: emulate enough OS behaviors (process scheduling, resource isolation, syscalls) to make each node feel like it runs a minimal operating system without building a full kernel.

### Virtual OS Key Design Elements

- **VirtualOS Layer**: create a `VirtualOS` class that attaches to each node and manages processes representing transfers, maintenance jobs, or admin tasks.
- **Process Scheduler**: cooperative round-robin or priority-based scheduler that runs within simulator ticks. Integrate CPU quotas so high transfer rates can starve CPU if not managed.
- **Memory Manager**: track RAM reservations per process; reject work if a node is oversubscribed.
- **Syscall Surface**: define a small API (e.g., `spawn_process`, `read_disk`, `network_send`) that CloudSim components must use, enabling future policy enforcement/logging.
- **Device Abstractions**: treat the network interface and storage device as drivers managed by `VirtualOS`, so we can simulate interrupts or device saturation.

### Virtual OS Milestones

1. Define `VirtualOS` data model and integrate with `StorageVirtualNode` lifecycle.
2. Implement process scheduler + CPU accounting; add tests that verify scheduling order.
3. Add memory quotas and failure modes (e.g., OOM events).
4. Route disk/network actions through the OS layer so policies apply uniformly.

## 3. Disk Storage Subsystem

**Goal**: replace abstract storage counters with a block-device simulator that stores actual data (optionally persisted) and enforces realistic I/O limits.

### Disk Key Design Elements

- **VirtualDisk**: represent disks as collections of blocks/sectors with configurable size, throughput, and latency. Provide in-memory backend first, then optional file-backed persistence.
- **Filesystem Layer (Optional)**: expose a simple hierarchical namespace (directories/files) so nodes can organize stored data beyond raw transfers.
- **I/O Scheduler**: queue reads/writes per disk, simulate seek/latency, and integrate with simulator ticks so large writes span multiple ticks.
- **Data Integrity**: compute checksums per block, surface corruption events, and allow repair workflows in future.
- **Persistence Hooks**: allow test fixtures to preload disks or dump state to disk for replay.

### Disk Milestones

1. Implement `VirtualDisk` with read/write/flush ops and integrate with `StorageVirtualNode`.
2. Add async I/O scheduling tied to simulator ticks; ensure bandwidth accounting matches network ingestion.
3. Optionally add filesystem metadata and persistence adapters.

## Integration & Ordering

1. **Disk subsystem first**: gives tangible data handling for later features.
2. **Routing/IP layer**: once nodes can store real data, multi-hop replication/routing becomes meaningful.
3. **Virtual OS**: sits on top to coordinate CPU/memory/disk/network resources cohesively. Building it last lets it orchestrate the richer subsystems above.

Each milestone should ship with targeted pytest coverage (e.g., routing path tests, OS scheduler tests, disk persistence tests) and README updates so collaborators can verify functionality.
