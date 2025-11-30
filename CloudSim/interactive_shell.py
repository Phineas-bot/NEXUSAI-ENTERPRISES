from __future__ import annotations

import cmd
import shlex
from pathlib import Path
from typing import Any, List

from controller import CloudSimController, parse_size


class CloudSimShell(cmd.Cmd):
    intro = "CloudSim interactive shell. Type 'help' for commands."
    prompt = "cloudsim> "

    def __init__(self):
        super().__init__()
        state_path = Path(__file__).resolve().parent / "cloudsim_state.json"
        self.controller = CloudSimController(enable_persistence=True, state_path=str(state_path))

    # Helpers ------------------------------------------------------------
    def _print(self, message: str) -> None:
        self.stdout.write(message + "\n")

    def _parse(self, arg: str) -> List[str]:
        try:
            return shlex.split(arg)
        except ValueError as exc:
            self._print(f"Parse error: {exc}")
            return []

    # Node commands ------------------------------------------------------
    def do_add(self, arg: str) -> None:
        """add NODE_ID [--storage 500] [--bandwidth 1000] [--cpu 8] [--memory 32] [--zone auto]

        Create a new node with the provided capacities. If --zone is omitted a random zone
        is selected automatically.
        """

        tokens = self._parse(arg)
        if not tokens:
            return
        node_id = tokens[0]
        opts = {
            "storage_gb": 500,
            "bandwidth_mbps": 1000,
            "cpu_capacity": 8,
            "memory_capacity": 32,
            "zone": None,
        }
        key = None
        for token in tokens[1:]:
            if token.startswith("--"):
                key = token[2:]
                continue
            if key == "storage":
                opts["storage_gb"] = int(token)
            elif key == "bandwidth":
                opts["bandwidth_mbps"] = int(token)
            elif key == "cpu":
                opts["cpu_capacity"] = int(token)
            elif key == "memory":
                opts["memory_capacity"] = int(token)
            elif key == "zone":
                opts["zone"] = token
        try:
            node = self.controller.add_node(node_id, **opts)
            zone_label = node.zone or "unassigned"
            self._print(f"Node '{node_id}' added in zone {zone_label}")
        except ValueError as exc:
            self._print(str(exc))

    def do_remove(self, arg: str) -> None:
        """remove NODE_ID -- delete a node from the network"""

        node_id = arg.strip()
        if not node_id:
            self._print("Usage: remove NODE_ID")
            return
        if self.controller.remove_node(node_id):
            self._print(f"Node '{node_id}' removed")
        else:
            self._print(f"Node '{node_id}' not found")

    def do_nodes(self, arg: str) -> None:
        """nodes [--all] -- list current nodes and their status (replicas hidden unless --all)"""

        tokens = self._parse(arg)
        include_replicas = bool(tokens and tokens[0] == "--all")
        rows = self.controller.list_node_status(include_replicas=include_replicas)
        if not rows:
            self._print("No nodes configured")
            return
        for row in rows:
            status = "online" if row.online else "offline"
            replica_hint = f" replica-of {row.replicas}" if row.replicas else ""
            self._print(
                f"{row.node_id:12} {status:8} zone {row.zone or 'n/a':12} "
                f"storage {row.storage_used}/{row.storage_total} bytes," 
                f" bandwidth {row.bandwidth_bps} bps{replica_hint}"
            )

    def do_clusters(self, arg: str) -> None:  # pylint: disable=unused-argument
        """clusters -- display root clusters and replicas"""

        clusters = self.controller.get_clusters()
        if not clusters:
            self._print("No clusters defined")
            return
        for root, members in clusters.items():
            self._print(f"{root}: {', '.join(members)}")

    def do_inspect(self, arg: str) -> None:
        """inspect NODE_ID -- display detailed telemetry for a node"""

        node_id = arg.strip()
        if not node_id:
            self._print("Usage: inspect NODE_ID")
            return
        info = self.controller.get_node_info(node_id)
        if not info:
            self._print(f"Node '{node_id}' not found")
            return

        status = "online" if info.get("online") else "offline"
        zone = info.get("zone") or "n/a"
        self._print(f"Node {node_id} ({status}) zone={zone}")
        used = info.get("used_storage", 0)
        total = info.get("total_storage", 0)
        available = info.get("available_storage", 0)
        self._print(f"  Storage: {used}/{total} bytes (available {available})")
        self._print(f"  Bandwidth: {info.get('bandwidth')} Mbps")

        neighbors = info.get("neighbors") or []
        self._print(f"  Neighbors: {', '.join(neighbors) if neighbors else 'none'}")

        replica_parent = info.get("replica_parent")
        replica_children = info.get("replica_children") or []
        if replica_parent or replica_children:
            parent_label = replica_parent or "none"
            children_label = ", ".join(replica_children) if replica_children else "none"
            self._print(f"  Replica parent: {parent_label}")
            self._print(f"  Replica children: {children_label}")

        stored_files = info.get("stored_files", [])
        if stored_files:
            self._print("  Stored files:")
            for entry in stored_files:
                name = entry.get("file_name") or entry.get("file_id")
                size = entry.get("size_bytes", 0)
                completed = entry.get("completed_at")
                self._print(f"    - {name} ({size} bytes, completed_at={completed})")
        else:
            self._print("  Stored files: none")

        active_transfers = info.get("active_transfers", [])
        if active_transfers:
            self._print("  Active transfers:")
            for transfer in active_transfers:
                file_id = transfer.get("file_id")
                status = transfer.get("status")
                size = transfer.get("size_bytes", 0)
                self._print(f"    - {file_id} ({size} bytes) status={status}")
        else:
            self._print("  Active transfers: none")

        telemetry = info.get("telemetry")
        if telemetry:
            self._print("  Telemetry:")
            for key, value in telemetry.items():
                self._print(f"    {key}: {value}")

    # Topology -----------------------------------------------------------
    def do_connect(self, arg: str) -> None:
        """connect NODE_A NODE_B [NODE_C ...] [--bandwidth 1000] [--latency 1.0]

        Connects adjacent node pairs (A-B, B-C, ...) in one command.
        """

        tokens = self._parse(arg)
        if len(tokens) < 2:
            self._print("Usage: connect NODE_A NODE_B [NODE_C ...] [--bandwidth Mbps] [--latency ms]")
            return

        node_ids: List[str] = []
        idx = 0
        while idx < len(tokens) and not tokens[idx].startswith("--"):
            node_ids.append(tokens[idx])
            idx += 1
        if len(node_ids) < 2:
            self._print("Provide at least two node IDs before any --options")
            return

        bandwidth = None
        latency = None
        key = None
        for token in tokens[idx:]:
            if token.startswith("--"):
                key = token[2:]
                continue
            if key == "bandwidth":
                bandwidth = int(token)
            elif key == "latency":
                latency = float(token)

        successes = []
        failures = []
        for left, right in zip(node_ids, node_ids[1:]):
            if self.controller.connect_nodes(left, right, bandwidth, latency):
                link = self.controller.network.nodes.get(left)
                bw_label = None
                latency_label = None
                if link and right in link.connections:
                    bw_label = max(1, int(link.connections[right] / 1_000_000))
                    latency_label = link.link_latencies.get(right, 0.0)
                extra = ""
                if bw_label is not None and latency_label is not None:
                    extra = f" ({bw_label} Mbps, {latency_label} ms)"
                successes.append(f"{left} <-> {right}{extra}")
            else:
                failures.append(f"{left} <-> {right}")

        if successes:
            self._print("Connected: " + "; ".join(successes))
        if failures:
            self._print("Failed: " + "; ".join(failures) + " (verify nodes exist)")

    def do_disconnect(self, arg: str) -> None:
        """disconnect NODE_A NODE_B -- remove a link"""

        tokens = self._parse(arg)
        if len(tokens) != 2:
            self._print("Usage: disconnect NODE_A NODE_B")
            return
        if self.controller.disconnect_nodes(tokens[0], tokens[1]):
            self._print("Link removed")
        else:
            self._print("No such link or nodes missing")

    # Transfers ----------------------------------------------------------
    def do_transfer(self, arg: str) -> None:
        """transfer SOURCE TARGET FILE SIZE -- start a file transfer (SIZE accepts B/KB/MB/GB)"""

        tokens = self._parse(arg)
        if len(tokens) < 4:
            self._print("Usage: transfer SRC DST filename size")
            return
        src, dst, filename, size = tokens[:4]
        size_bytes = parse_size(size)
        try:
            transfer = self.controller.initiate_transfer(src, dst, filename, size_bytes)
            self.controller.run_until_idle()
            self._print(
                f"Transfer {transfer.file_name} status {transfer.status.name},"
                f" completed_at={transfer.completed_at}"
            )
        except RuntimeError as exc:
            self._print(str(exc))

    # Failures -----------------------------------------------------------
    def do_fail(self, arg: str) -> None:
        """fail NODE_ID -- mark a node offline"""

        node_id = arg.strip()
        if not node_id:
            self._print("Usage: fail NODE_ID")
            return
        if self.controller.fail_node(node_id):
            self._print(f"Node '{node_id}' failed")
        else:
            self._print(f"Node '{node_id}' not found")

    def do_restore(self, arg: str) -> None:
        """restore NODE_ID -- bring a failed node online"""

        node_id = arg.strip()
        if not node_id:
            self._print("Usage: restore NODE_ID")
            return
        self.controller.restore_node(node_id)
        self._print(f"Node '{node_id}' restored")

    # Simulation ---------------------------------------------------------
    def do_step(self, arg: str) -> None:
        """step [seconds] -- advance the simulator by the given duration (default 1s)"""

        duration = 1.0
        if arg.strip():
            duration = float(arg.strip())
        self.controller.run_for(duration)
        self._print(f"Advanced simulation by {duration} seconds")

    def do_events(self, arg: str) -> None:
        """events [count] -- show recent events"""

        count = int(arg.strip() or 10)
        events = self.controller.recent_events(count)
        if not events:
            self._print("No events yet")
            return
        for event in events:
            event_type = event.get("type", "unknown")
            timestamp = event.get("time", 0.0)
            details = {k: v for k, v in event.items() if k not in {"type", "time"}}
            self._print(f"[{timestamp:0.2f}s] {event_type} {details}")

    # Exit ---------------------------------------------------------------
    def do_exit(self, arg: str) -> bool:  # pylint: disable=unused-argument
        """exit -- leave the shell"""

        self._print("Exiting CloudSim shell")
        return True

    do_quit = do_exit


def launch_shell() -> None:
    CloudSimShell().cmdloop()
