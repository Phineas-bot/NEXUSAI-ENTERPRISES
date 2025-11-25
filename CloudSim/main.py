from __future__ import annotations

import argparse
import json
from typing import List

from demo_scenarios import SCENARIOS, run_scenario


def _print_transfer_stats(transfers: List[dict]) -> None:
    if not transfers:
        print("  No transfers recorded.")
        return
    for transfer in transfers:
        duration = transfer.get("duration")
        duration_text = f"{duration:.2f}s" if duration is not None else "n/a"
        print(
            "  - {file} | {status} | {size_mb:.1f} MB | duration {duration}".format(
                file=transfer.get("file"),
                status=transfer.get("status"),
                size_mb=transfer.get("size_bytes", 0) / (1024 * 1024),
                duration=duration_text,
            )
        )


def _print_cluster(cluster: dict) -> None:
    if not cluster:
        return
    print(f"  Root: {cluster.get('root')}")
    replicas = cluster.get("replicas", [])
    print(f"  Replicas: {', '.join(replicas) if replicas else 'none'}")
    parents = cluster.get("replica_parents", {})
    if parents:
        print(f"  Replica parents: {parents}")
    triggers = cluster.get("last_triggers", {})
    if triggers:
        print(f"  Last triggers: {triggers}")


def _print_routes(routes: dict) -> None:
    if not routes:
        return
    print(f"  Initial route: {routes.get('initial')}")
    print(f"  After failure: {routes.get('after_failure')}")


def _print_summary(summary: dict) -> None:
    print(f"\n=== Scenario: {summary.get('scenario')} ===")
    if summary.get("events"):
        print("  Sample events:")
        for line in summary["events"]:
            print(f"    {line}")
    if "transfers" in summary:
        print("  Transfers:")
        _print_transfer_stats(summary.get("transfers", []))
    if "cluster" in summary:
        print("  Cluster snapshot:")
        _print_cluster(summary["cluster"])
    if "routes" in summary:
        print("  Routes:")
        _print_routes(summary["routes"])
    if "metrics" in summary:
        print(f"  Metrics: {summary['metrics']}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CloudSim scenario runner")
    parser.add_argument(
        "--scenario",
        choices=["all", *SCENARIOS.keys()],
        default="hotspot",
        help="Name of the scenario to execute",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=25,
        help="Maximum number of events to capture per scenario",
    )
    parser.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON summaries instead of formatted text",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    if args.list:
        print("Available scenarios:")
        for name in SCENARIOS:
            print(f"  - {name}")
        return

    scenario_names = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]

    summaries = []
    for name in scenario_names:
        summaries.append(run_scenario(name, event_limit=args.max_events))

    if args.json:
        print(json.dumps(summaries, indent=2))
        return

    for summary in summaries:
        _print_summary(summary)


if __name__ == "__main__":
    main()