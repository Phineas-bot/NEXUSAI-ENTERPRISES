import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from CloudSim.demo_scenarios import (
    run_disk_failure_demo,
    run_hotspot_scaling_demo,
    run_routing_failover_demo,
    run_scenario,
)


def test_hotspot_demo_creates_replicas():
    summary = run_hotspot_scaling_demo()
    replicas = summary["cluster"]["replicas"]
    assert replicas, "expected at least one replica in hotspot scenario"
    for transfer in summary["transfers"]:
        assert transfer["status"] == "COMPLETED"


def test_disk_failure_demo_reports_os_failures():
    summary = run_disk_failure_demo()
    assert summary["metrics"]["os_failures"] >= 1
    triggers = summary["cluster"]["last_triggers"].values()
    assert any(trigger == "os_failures" for trigger in triggers if trigger)


def test_routing_failover_demo_changes_route():
    summary = run_routing_failover_demo()
    routes = summary["routes"]
    assert routes["initial"] != routes["after_failure"]


def test_run_scenario_validates_names():
    try:
        run_scenario("invalid")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown scenario")
