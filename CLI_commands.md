# CloudSim Interactive Shell Guide

## TL;DR

- Launch with `python CloudSim/main.py` (interactive is the default mode).
- Use `add node`, `connect`, `transfer`, `push`, and `fetch` to model clusters, data motion, uploads, and on-demand pulls (multi-hop `connect` accepts more than two nodes).
- `inspect`, `nodes --all`, `fail`, `restore`, and `events` let you observe health and resiliency behaviors.
- `save`, `load`, and `reset --clear` manage persistent snapshots so labs survive terminal restarts.
- Type `help` or `help <command>` at any time for inline documentation.

---

## Setup Checklist

1. Activate your virtual environment: `.& .venv\Scripts\Activate.ps1` (or use conda/system Python).
2. Install dependencies once: `pip install -r requirements.txt`.
3. Run all commands from the repo root (`C:\Users\...\NEXUSAI-ENTERPRISES`).

## Essential Concepts

- **Nodes** represent storage endpoints with capacity, zone, and health state.
- **Links** define bandwidth/latency between nodes and influence transfer throughput.
- **Transfers** break multi-terabyte jobs into chunks so you can watch scheduling decisions.
- **Events & inspections** capture what just happened—great for demos and debugging.

## Automatic Allocations

- **Zones:** Every node you add is dropped into a randomly selected cloud-like zone (override with `--zone`). The zone drives latency and bandwidth heuristics.
- **Links:** When you connect nodes without specifying `--bandwidth`/`--latency`, CloudSim infers realistic values based on whether the nodes share a zone, a region, or a continent.
- **Chunks:** Transfers auto-size their chunks by analyzing file size, hop count, and route bottlenecks, so cross-region copies automatically use smaller, more responsive chunks.
- **Auto wiring sanity checks:** Use `nodes --all` or `inspect <node>` after an `add`/`connect` to see which zones and link metrics were assigned automatically.

You can still override any of these by passing explicit flags, but the defaults keep the REPL snappy for exploratory work.

## Persistent Workspaces

- The shell now autosaves to `CloudSim/cloudsim_state.json` so clusters, files, and events survive after you exit.
- Run `save <path>` to snapshot the current topology elsewhere (useful when preparing multiple demos).
- Use `load <path>` to switch to another workspace; the autosave target follows the last loaded or saved path.
- `reset` starts from a clean slate while keeping the snapshot file; add `--clear` to delete the saved state entirely.

## Five-Minute Interactive Tour

Paste each block at the `cloudsim>` prompt to experience the main workflows. Sample output is abbreviated for clarity.

1. **Bring two zones online**

    ```text
    add west-a --storage 10000 --zone us-west-1a
    add east-a --storage 12000 --zone us-east-1a
    ```

2. **Wire them together**

    ```text
    connect west-a east-a --bandwidth 5000 --latency 25
    nodes
    nodes --all   # include replicas
    ```

3. **Push data across the country**

    ```text
    transfer west-a east-a dataset-alpha.bin 1.5TB
    inspect east-a
    ```

4. **Inject a failure and inspect history**

    ```text
    fail east-a
    events 5
    nodes
    ```

5. **Recover, preserve, and reload later**

    ```text
    restore east-a
    save demos/west-vs-east.json
    reset --clear
    load demos/west-vs-east.json
    quit
    ```

## Command Cheat Sheet

| Command | Why you use it | Example |
| --- | --- | --- |
| `add <name> [--storage <GB>] [--bandwidth <Mbps>] [--cpu <vCPU>] [--memory <GB>] [--zone <zone>]` | Introduce capacity in a specific or auto-assigned zone. | `add core-1 --storage 8000 --zone eu-central-1a` |
| `remove <name>` | Cleanly decommission a node. | `remove old-cache` |
| `connect <nodeA> <nodeB> [nodeC ...] [--bandwidth <Mbps>] [--latency <ms>]` | Link adjacent pairs; omit flags to let CloudSim infer metrics. | `connect core-1 edge-2 archive-1 --latency 5` |
| `disconnect <nodeA> <nodeB>` | Break a link to force reroutes. | `disconnect core-1 edge-2` |
| `transfer <src> <dst> <filename> <size>` | Simulate workload movement (size accepts `MB/GB/TB`). | `transfer ingest archive media-sync.bin 900GB` |
| `push <src> <filename> <size> [--local]` | Upload a file without naming the destination (CloudSim auto-selects a node; `--local` keeps it on the uploader). | `push ingest dataset-alpha.bin 1TB` |
| `fetch <target> <filename>` | Pull an existing dataset into a node without knowing where it currently lives (matches by name/id). | `fetch edge-cache dataset-alpha.bin` |
| `fail <name>` | Take a node offline to showcase resiliency. | `fail edge-2` |
| `restore <name>` | Return a failed node to service. | `restore edge-2` |
| `nodes [--all]` | List nodes with health, capacity, utilization (use `--all` to include replicas). | `nodes --all` |
| `inspect <name>` | Dump per-node telemetry, stored files, replica tree, and neighbors. | `inspect archive-1` |
| `events [count]` | Tail the controller event log. | `events 10` |
| `step [seconds]` | Advance the simulator clock manually for deterministic demos. | `step 5` |
| `save [path]` | Persist the current workspace to JSON (default path is `CloudSim/cloudsim_state.json`). | `save backups/lab1.json` |
| `load [path]` | Restore a snapshot and point autosave at it. | `load backups/lab1.json` |
| `reset [--clear]` | Reset to a blank topology; add `--clear` to delete the snapshot file too. | `reset --clear` |
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
nodes --all      # look for high utilization or failed replicas
inspect node-a   # confirm stored files and replica parents
events --tail 5  # correlate with recent actions
```

## Troubleshooting

- **`python` not found**: ensure the virtual environment is activated or use the full path `.& .venv\Scripts\python.exe`.
- **Command not recognized**: run `help` to verify the exact syntax; options always use double hyphens.
- **Nothing happens after a command**: for long transfers use `inspect <target>` / `events` to monitor progress, or `step 5` if you disabled auto-advancement.
- **Need to reset**: exit the shell and relaunch; every session starts with a clean simulator unless your script replays prior state.

With these shortcuts you can demonstrate CloudSim’s capabilities in a few minutes, then dive deeper by exploring `CloudSim/interactive_shell.py` for new automation ideas.
