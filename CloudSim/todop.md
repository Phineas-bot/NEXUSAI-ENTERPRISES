# CloudSim – Phased Roadmap (Distributed Systems Simulator)

Purpose: evolve this repo into a deterministic, classroom‑ready cloud/distributed‑systems simulator that models nodes, links, message transfer, and storage behaviors.

---

## Phase 0 – Baseline & Scope

- [ ] Scenarios: single-hop/multi-hop transfers, replication, failures, leader election, gossip.
- [ ] Time model: choose discrete‑event simulation (deterministic, fast) with seeded RNG.
- [ ] Repo hygiene: fill `README.md` (goals, quickstart), link architecture diagram.
- [ ] Coding standards: typing, formatting, unit tests policy.

## Phase 1 – Simulation Engine

- [ ] `Simulator` with simulated clock, `heapq` priority queue, seeded RNG.
- [ ] `Event` dataclass: `time`, `priority`, `type`, `payload`, `callback`.
- [ ] APIs: `schedule_at(t, fn, *args)`, `schedule_in(dt, fn, *args)`, `run(until=None, max_events=None)`.
- [ ] Tracing hooks on schedule/execute; pause/resume; deterministic mode.
- [ ] Replace `time.sleep` with scheduled events.

## Phase 2 – Network Layer

- [ ] `Link` model: bandwidth (bps), latency (ms), jitter, loss, queue discipline (FIFO now; token‑bucket shaping later).
- [ ] Topologies: star, ring, mesh, random (ER), k‑ary fat‑tree; multi‑DC latency matrices.
- [ ] Routing: shortest path (Dijkstra); optional ECMP/K‑shortest paths.
- [ ] Message/Packet: envelope (id, src, dst, size, type, headers), fragmentation/reassembly rules.
- [ ] NIC buffers and backpressure; per‑link queues; drops and (optional) retransmission.

## Phase 3 – Node Model & Roles

- [ ] `BaseNode`: resources (CPU, RAM, disk), in/out queues, timers, message handlers, failure state.
- [ ] Roles: `StorageNode` (refactor existing), `ClientNode` (workload gen), `ControlNode` (metadata/coordination), optional `ComputeNode`.
- [ ] Membership: static config first; SWIM/gossip later.
- [ ] Health states: running, degraded, down; restart behavior.

## Phase 4 – Storage Service

- [ ] Metadata service: file catalog, chunk map, replica locations (centralized or consistent hashing ring).
- [ ] Write path: chunk sizing policy, placement (rack/zone aware), replication factor N, ack strategy.
- [ ] Read path: best replica selection (latency/queue aware), hedged reads (optional).
- [ ] Consistency modes: eventual; quorum (W/R); strong (single leader) – pluggable.
- [ ] Optional: erasure coding (RS) as a strategy plug‑in.

## Phase 5 – Messaging Patterns

- [ ] RPC atop message layer with timeouts, retries, exponential backoff, idempotency keys.
- [ ] Gossip for membership/health/metrics; anti‑entropy sync.
- [ ] Pub/Sub or queue semantics to compare delivery guarantees (at‑least‑once vs at‑most‑once).

## Phase 6 – Algorithms & Protocols (pick 1–2 to implement deeply)

- [ ] Raft‑lite: leader election, heartbeats, log append/commit for metadata store.
- [ ] OR Consistent hashing/DHT (Chord‑lite): keyspace partitioning, joins/leaves, finger tables.
- [ ] Optional: simple scheduler for data‑local task placement.

## Phase 7 – Failures & Chaos Engineering

- [ ] Fault injection: node crash/reboot, link down/partition, latency/loss spikes, packet corruption.
- [ ] Recovery: re‑replication, leader re‑election, retries with jittered backoff.
- [ ] Load sheddding: queue limits, backpressure behavior under overload.

## Phase 8 – Observability & UX

- [ ] Metrics: per‑node/per‑link counters (throughput, latency, queue length, CPU/storage usage); histograms/P50/P95/P99.
- [ ] Tracing: event log with spans (send→deliver→ack) and correlation ids; JSON export.
- [ ] Visualization: topology export (Graphviz), time‑series export; simple TUI dashboard; optional notebook plots.
- [ ] Deterministic replays via seed + recorded event log.

## Phase 9 – Config, Scenarios & Workloads

- [ ] Scenario YAML/JSON: nodes, links, topology, workloads, fault schedule, RNG seed, run duration.
- [ ] Workloads: read/write mixes; size distributions (Pareto/lognormal), burstiness; multi‑client generators.
- [ ] Canned scenarios for labs (single DC vs multi‑DC, with/without failures, different consistency modes).

## Phase 10 – Code Quality & Packaging

- [ ] Project layout: `CloudSim/{simulator.py, network/, node/, storage/, protocols/, scenarios/, cli.py}`.
- [ ] Tooling: ruff/flake8, mypy/pyright, black; `pytest` + coverage.
- [ ] CLI: `cloudsim run -c scenario.yaml --seed 42 --until 120s`.
- [ ] CI: run lint/tests; publish artifacts (logs/plots) on failure for debugging.

---

## Immediate Refactors (map to current code)

- [ ] Remove `time.sleep` in `storage_virtual_node.py`; use `Simulator.schedule_in` for chunk transfer time.
- [ ] Move link bandwidth/latency to `Link`; keep nodes unaware of path details beyond routing.
- [ ] `StorageVirtualNetwork` becomes an orchestrator using `Simulator`; introduce routing and link registry.
- [ ] Add `get_state()/to_dict()` on nodes/network for tests and visualization.

## Milestones

- M1: Simulator + single‑hop transfer deterministically completes; unit tests for chunking and timing.
- M2: Network layer with links/topology/routing; concurrent flows share bandwidth fairly.
- M3: Storage service with replication and basic consistency; failure‑free happy path.
- M4: Failure injection + recovery; metrics/tracing + CLI scenarios.

## Testing Targets

- [ ] Single file transfer completes with expected simulated time.
- [ ] Concurrent transfers share link bandwidth within tolerance.
- [ ] Partition + rejoin triggers re‑replication or leader re‑election.
- [ ] Quorum write/read satisfies chosen W/R parameters across failures.

## Notes

- Keep simulation deterministic by default (no wall‑clock waits); use seeds for reproducibility.
- Prefer small, composable interfaces; keep storage, network, and protocols decoupled via events.
