# CloudSim Interactive Shell Tutorial

## Overview

CloudSim ships with an interactive REPL that lets you manage storage nodes, links, transfers, and failure simulations without editing code. This tutorial explains how to launch the shell, demonstrates a realistic operator session, and documents the most useful commands so you can experiment quickly.

## Prerequisites

- Python environment activated (`.venv` or your own interpreter)
- Project dependencies installed (see `README.md` for `pip install -r requirements.txt` if needed)
- Repository root as the working directory so relative paths resolve correctly

## Launching the Interactive Shell

From the repository root, run:

```powershell
python CloudSim/main.py
```

By default the script enters interactive mode. If you ever need to force it explicitly (for clarity in documentation or scripts), append `--mode interactive`.

## Quickstart Walkthrough

Follow this mini-scenario to get familiar with core operations. Enter each command at the `cloudsim>` prompt; output is representative and may vary slightly.

1. **Create the first storage node**

    ```text
    add node west-a --capacity 10TB --zone us-west-1a
    ```

2. **Add a second node in another zone**

    ```text
    add node east-a --capacity 12TB --zone us-east-1a
    ```

3. **Link the nodes with desired bandwidth/latency**

    ```text
    connect west-a east-a --bandwidth 5Gbps --latency 25ms
    ```

4. **Verify cluster state**

    ```text
    nodes
    clusters
    ```

5. **Initiate a multi-chunk transfer**

    ```text
    transfer dataset-alpha --src west-a --dst east-a --size 1.5TB --chunks 6
    ```

6. **Simulate a failure and observe telemetry**

    ```text
    fail node east-a --reason planned-maintenance
    telemetry
    events
    ```

7. **Restore the node and re-run replication**

    ```text
    restore node east-a
    transfer dataset-alpha --src west-a --dst east-a --size 500GB --chunks 4
    ```

8. **Exit when done**

    ```text
    quit
    ```

This flow ensures you touch node lifecycle, networking, data motion, failures, and observability in a single sitting.

## Command Reference Highlights

Use `help` inside the shell for the full list. The table below summarizes the most common commands.

| Command | Purpose | Example |
| --- | --- | --- |
| `add node <name> [--capacity <bytes>] [--zone <zone>]` | Creates a storage node with optional capacity shorthand (`500GB`, `12TB`). | `add node core-1 --capacity 8TB --zone eu-central-1a` |
| `remove node <name>` | Deletes a node (must be offline or drained). | `remove node old-cache` |
| `connect <nodeA> <nodeB> [--bandwidth <bps>] [--latency <ms>]` | Establishes a bidirectional link with telemetry-aware settings. | `connect core-1 edge-2 --bandwidth 2Gbps --latency 10ms` |
| `disconnect <nodeA> <nodeB>` | Removes a link, forcing reroutes. | `disconnect core-1 edge-2` |
| `transfer <label> --src <node> --dst <node> --size <bytes> [--chunks <n>]` | Queues a logical dataset transfer with optional chunk count for granular tracking. | `transfer media-sync --src ingest --dst archive --size 900GB --chunks 9` |
| `fail node <name> [--reason <text>]` | Marks a node offline and triggers reroute/replica behaviors. | `fail node edge-2 --reason link-flap` |
| `restore node <name>` | Clears failure state and returns node to service. | `restore node edge-2` |
| `nodes` | Lists nodes with health, capacity, and utilization metrics. | `nodes` |
| `links` | Displays network connections with bandwidth/latency. | `links` |
| `telemetry` | Prints recent throughput, latency, queue depth, and replica stats. | `telemetry` |
| `events [--tail <n>]` | Shows recent controller events for auditing. | `events --tail 10` |
| `step [--ticks <n>]` | Advances the simulator clock when auto-step is disabled. | `step --ticks 5` |
| `quit` | Exits the shell. | `quit` |

## Tips

- **Human-friendly sizes:** Capacity and transfer sizes accept suffixes (`MB`, `GB`, `TB`, `PB`).
- **Tab completion:** Windows terminals support basic history; use the up arrow to reuse complex commands.
- **Repeatable demos:** Combine shell commands with `source <file>` to replay scripted scenarios (create a text file with one command per line).
- **JSON snapshots:** Outside the shell, run `python CloudSim/main.py --mode scenario --scenario hotspot --json` to capture structured output for dashboards.

You now have a practical cheat sheet for operating the CloudSim interactive shell. Explore additional commands via `help` or dive into `CloudSim/interactive_shell.py` for implementation details.
