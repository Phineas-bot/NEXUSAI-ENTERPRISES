# CloudSim Interactive Shell Guide

## TL;DR

- Launch with `python CloudSim/main.py` (interactive is the default mode).
- Use `add node`, `connect`, and `transfer` to model clusters and data motion.
- `fail node`, `restore`, `telemetry`, and `events` let you observe resiliency behaviors.
- Type `help` or `help <command>` at any time for inline documentation.

---

## Setup Checklist

1. Activate your virtual environment: `.& .venv\Scripts\Activate.ps1` (or use conda/system Python).
2. Install dependencies once: `pip install -r requirements.txt`.
3. Run all commands from the repo root (`C:\Users\...\NEXUSAI-ENTERPRISES`).

## Starting the Shell

```powershell
python CloudSim/main.py
```

Add `--mode interactive` only if you are scripting the launch and want to be explicit. The prompt changes to `cloudsim>` when the REPL is ready.

## Essential Concepts

- **Nodes** represent storage endpoints with capacity, zone, and health state.
- **Links** define bandwidth/latency between nodes and influence transfer throughput.
- **Transfers** break multi-terabyte jobs into chunks so you can watch scheduling decisions.
- **Events/telemetry** capture what just happened—great for demos and debugging.

## Five-Minute Interactive Tour

Paste each block at the `cloudsim>` prompt to experience the main workflows. Sample output is abbreviated for clarity.

1. **Bring two zones online**

    ```text
    add node west-a --capacity 10TB --zone us-west-1a
    add node east-a --capacity 12TB --zone us-east-1a
    ```

2. **Wire them together**

    ```text
    connect west-a east-a --bandwidth 5Gbps --latency 25ms
    nodes
    links
    ```

3. **Push data across the country**

    ```text
    transfer dataset-alpha --src west-a --dst east-a --size 1.5TB --chunks 6
    telemetry
    ```

4. **Inject a failure and inspect history**

    ```text
    fail node east-a --reason planned-maintenance
    events --tail 5
    telemetry
    ```

5. **Recover and resume replication**

    ```text
    restore node east-a
    transfer dataset-alpha --src west-a --dst east-a --size 500GB --chunks 4
    quit
    ```

## Command Cheat Sheet

| Command | Why you use it | Example |
| --- | --- | --- |
| `add node <name> [--capacity <bytes>] [--zone <zone>]` | Introduce capacity in a zone; accepts `GB/TB/PB` shorthands. | `add node core-1 --capacity 8TB --zone eu-central-1a` |
| `remove node <name>` | Cleanly decommission a node. | `remove node old-cache` |
| `connect <nodeA> <nodeB> [--bandwidth <bps>] [--latency <ms>]` | Control link characteristics for routing demos. | `connect core-1 edge-2 --bandwidth 2Gbps --latency 10ms` |
| `disconnect <nodeA> <nodeB>` | Break a link to force reroutes. | `disconnect core-1 edge-2` |
| `transfer <label> --src <node> --dst <node> --size <bytes> [--chunks <n>]` | Simulate workload movement and observe chunk scheduling. | `transfer media-sync --src ingest --dst archive --size 900GB --chunks 9` |
| `fail node <name> [--reason <text>]` | Take a node offline to showcase resiliency. | `fail node edge-2 --reason link-flap` |
| `restore node <name>` | Return a failed node to service. | `restore node edge-2` |
| `nodes` | List nodes with health, capacity, utilization. | `nodes` |
| `links` | Inspect network topology. | `links` |
| `telemetry` | Show recent throughput, latency, replica decisions. | `telemetry` |
| `events [--tail <n>]` | Tail the controller event log. | `events --tail 10` |
| `step [--ticks <n>]` | Advance the simulator clock manually (useful for scripted demos). | `step --ticks 5` |
| `source <file>` | Replay commands from a script file. | `source scripts/lab1.txt` |
| `quit` | Exit the shell. | `quit` |

## Handy Recipes

### Script a Demo

1. Create `scripts/burst.txt` with one command per line (add nodes, transfers, failures).
2. Launch the shell and run `source scripts/burst.txt` to replay consistently during presentations.

### Capture JSON for Dashboards

Outside the shell you can run:

```powershell
python CloudSim/main.py --mode scenario --scenario hotspot --json
```

The JSON payload is perfect for Grafana/Power BI mockups.

### Spot Bottlenecks Quickly

```text
nodes            # look for high utilization
links            # check saturated bandwidth
telemetry        # confirm throughput and latency trends
events --tail 5  # correlate with recent actions
```

## Troubleshooting

- **`python` not found**: ensure the virtual environment is activated or use the full path `.& .venv\Scripts\python.exe`.
- **Command not recognized**: run `help` to verify the exact syntax; options always use double hyphens.
- **Nothing happens after a command**: for long transfers use `telemetry`/`events` to monitor progress, or `step --ticks 5` if you disabled auto-advancement.
- **Need to reset**: exit the shell and relaunch; every session starts with a clean simulator unless your script replays prior state.

With these shortcuts you can demonstrate CloudSim’s capabilities in a few minutes, then dive deeper by exploring `CloudSim/interactive_shell.py` for new automation ideas.
