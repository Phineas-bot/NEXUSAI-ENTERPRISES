from __future__ import annotations

import cmd
import shlex
from typing import Any, List

from controller import CloudSimController, parse_size


class CloudSimShell(cmd.Cmd):
    intro = "CloudSim interactive shell. Type 'help' for commands."
    prompt = "cloudsim> "

    def __init__(self):
        super().__init__()
        self.controller = CloudSimController()

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
        """add NODE_ID [--storage 500] [--bandwidth 1000] [--cpu 8] [--memory 32]

        Create a new node with the provided capacities.
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
        try:
            self.controller.add_node(node_id, **opts)
            self._print(f"Node '{node_id}' added")
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

    def do_nodes(self, arg: str) -> None:  # pylint: disable=unused-argument
        """nodes -- list current nodes and their status"""

        rows = self.controller.list_node_status()
        if not rows:
            self._print("No nodes configured")
            return
        for row in rows:
            status = "online" if row.online else "offline"
            self._print(
                f"{row.node_id:12} {status:8} storage {row.storage_used}/{row.storage_total} bytes,"
                f" bandwidth {row.bandwidth_bps} bps"
            )

    def do_clusters(self, arg: str) -> None:  # pylint: disable=unused-argument
        """clusters -- display root clusters and replicas"""

        clusters = self.controller.get_clusters()
        if not clusters:
            self._print("No clusters defined")
            return
        for root, members in clusters.items():
            self._print(f"{root}: {', '.join(members)}")

    # Topology -----------------------------------------------------------
    def do_connect(self, arg: str) -> None:
        """connect NODE_A NODE_B [--bandwidth 1000] [--latency 1.0]"""

        tokens = self._parse(arg)
        if len(tokens) < 2:
            self._print("Usage: connect NODE_A NODE_B [--bandwidth Mbps] [--latency ms]")
            return
        node_a, node_b = tokens[:2]
        bandwidth = 1000
        latency = 1.0
        key = None
        for token in tokens[2:]:
            if token.startswith("--"):
                key = token[2:]
                continue
            if key == "bandwidth":
                bandwidth = int(token)
            elif key == "latency":
                latency = float(token)
        if self.controller.connect_nodes(node_a, node_b, bandwidth, latency):
            self._print(f"Connected {node_a} <-> {node_b}")
        else:
            self._print("Connection failed; ensure both nodes exist")

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
