# How the CloudSim System Works

This document explains, in plain language, what the current CloudSim system does and how its main pieces work together.

---

## 1. What CloudSim Does (High‑Level)

CloudSim is a **local simulator for a distributed storage cloud**. It lets you:

- Create and remove **storage nodes** (like mini data‑centers or racks).
- Connect nodes with **network links** that have bandwidth and latency.
- Send **data transfers** between nodes, optionally broken into chunks.
- Automatically maintain **replicas** and react to node failures.
- Observe the system through **telemetry and events**.

You can drive it in two ways:

1. **Interactive shell (default)** – a REPL where you type commands such as `add node`, `connect`, `transfer`, `fail node`, etc., and watch the system react in real time.
2. **Predefined scenarios** – scripted demos (like the *hotspot* scenario) that automatically build a topology, push traffic, and then print a summary or JSON report.

The goal is to give you a **fast, visualizable sandbox** for reasoning about capacity planning, data replication, and failure handling without needing a real cloud.

---

## 2. Main Building Blocks

At a code level, CloudSim is built from a few core components.

### 2.1 `StorageVirtualNode`

Represents a single storage node:

- Has a **name**, **capacity** (e.g. `10TB`), and **zone** (e.g. `us-west-1a`).
- Tracks **used vs. free capacity** and health (`online` / `failed`).
- Participates in **replication** and **routing** decisions.

### 2.2 `StorageVirtualNetwork`

Represents the network and cluster topology:

- Keeps a registry of all **nodes**.
- Stores **links** between nodes with bandwidth and latency.
- Knows how to **route transfers** and calculate their cost/speed.

This layer is where CloudSim reasons about “can this node talk to that node, and how fast?”

### 2.3 Simulator / Controller

Sitting on top of the network, there is a **controller** (used by the interactive shell) and a **simulator loop** (used by scenarios):

- The **controller** exposes imperative operations like:
	- add / remove nodes
	- connect / disconnect links
	- start transfers
	- mark nodes as failed or restored
	- query telemetry and recent events
- The **simulator loop** advances time, moves chunks, updates metrics, and triggers automatic behaviors such as:
	- replica placement / rebalancing
	- rerouting when nodes or links fail
	- adjusting throughput based on available bandwidth and latency

### 2.4 Interactive Shell

The interactive shell is a small CLI program on top of the controller:

- Reads commands like `add node`, `nodes`, `transfer`, `telemetry`.
- Validates arguments and calls the appropriate controller methods.
- Prints **human‑readable output** and logs events so you can see what happened.

When you run:

```powershell
python CloudSim/main.py
```

it launches this shell by default and drops you into a `cloudsim>` prompt.

### 2.5 Scenario Runner

For demos and automated checks, there is a **scenario runner**:

- Reads a selected scenario name (for example `hotspot`).
- Builds a predefined topology and traffic pattern.
- Runs the simulator loop to completion.
- Prints a **summary** of transfers, replica placement, and bottlenecks. With `--json`, it returns a structured payload suitable for dashboards or tests.

---

## 3. How a Typical Interactive Session Works

This section walks through what actually happens inside the system when you use the interactive shell.

### Step 1 – You add nodes

Example command:

```text
add node west-a --capacity 10TB --zone us-west-1a
add node east-a --capacity 12TB --zone us-east-1a
```

Inside CloudSim:

- The shell parses the commands and calls the controller.
- The controller creates two `StorageVirtualNode` instances and registers them with the `StorageVirtualNetwork`.
- Each node starts as **online** with **zero data used**.

### Step 2 – You connect nodes

```text
connect west-a east-a --bandwidth 5Gbps --latency 25ms
```

Inside CloudSim:

- The controller adds a **link** between `west-a` and `east-a` into the `StorageVirtualNetwork`.
- The link stores its **bandwidth** and **latency**, which the simulator later uses to estimate how quickly chunks can move.

### Step 3 – You start a transfer

```text
transfer dataset-alpha --src west-a --dst east-a --size 1.5TB --chunks 6
```

Inside CloudSim:

- The controller converts the size (`1.5TB`) into bytes and splits it into 6 **chunks**.
- A transfer job is created with per‑chunk metadata (source, destination, remaining bytes, current status).
- The job is handed off to the simulator loop.

### Step 4 – The simulator moves data and updates metrics

On each simulation tick:

- The simulator looks at **active transfers**, available **bandwidth** on links, and **node health**.
- It moves bytes for each chunk according to link capacity and latency.
- It updates:
	- how many bytes each node is storing,
	- progress of each chunk,
	- throughput and latency metrics,
	- any triggered **replication / rebalancing** actions.
- It records **events** such as “chunk 3 completed” or “replica created on node X”.

When all chunks finish, the transfer is marked **complete**, and telemetry shows the final statistics.

### Step 5 – You inject failures and recoveries

Example:

```text
fail node east-a --reason planned-maintenance
```

Inside CloudSim:

- The node’s health flips to **failed/offline**.
- The simulator stops routing new traffic to that node.
- If replicas were stored there, automatic behaviors may schedule **new replicas** elsewhere to keep the desired redundancy.
- An event is logged so `events` and `telemetry` reflect the incident.

When you later run:

```text
restore node east-a
```

the node’s health is set back to **online**, and it can again participate in routing and replication.

### Step 6 – You inspect the system state

Common read‑only commands and what they show:

- `nodes` – node list with health and capacity usage.
- `links` – all network links with bandwidth/latency.
- `telemetry` – recent throughput, latency, and replica stats.
- `events --tail N` – the last N events (transfers, failures, recoveries, replica moves).

These views are all driven by the same underlying controller and simulator state.

---

## 4. How Scenarios Work Internally

When you run a scenario instead of the shell, for example:

```powershell
python CloudSim/main.py --mode scenario --scenario hotspot
```

CloudSim does roughly this:

1. **Initializes** a fresh `StorageVirtualNetwork` and simulator.
2. **Builds** a topology and a bursty traffic pattern representing a “hotspot” workload.
3. **Runs** the simulator loop for a fixed amount of simulated time.
4. **Collects metrics**: completed transfers, per‑node load, replica layout, bottleneck links.
5. **Prints a summary** (human‑readable or JSON if `--json` is given).

Internally, the same building blocks are used as in interactive mode—the only difference is that the scenario script issues the operations instead of your shell commands.

---

## 5. Mental Model to Remember

You can think of CloudSim as:

> **A time‑driven simulation of nodes, links, and data replicas, controlled either by a live shell or by scripted scenarios.**

Nodes and links define **where** data can live and travel.

Transfers and replicas define **what** data moves and how redundant it is.

The controller, simulator loop, telemetry, and events define **how** everything evolves over simulated time.

With this model in mind, both the interactive shell and the scenario runner are just two different front‑ends to the same core engine.

