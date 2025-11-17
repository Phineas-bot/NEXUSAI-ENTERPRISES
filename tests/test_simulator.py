import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import Simulator  # noqa: E402


def test_simulator_respects_time_and_priority_ordering():
    sim = Simulator()
    trace = []

    def record(label: str) -> None:
        trace.append((label, sim.now))

    sim.schedule_in(0.1, record, "delta")
    sim.schedule_in(0.05, record, "beta", priority=2)
    sim.schedule_at(0.05, record, "alpha")
    sim.schedule_in(0.05, record, "prio", priority=-1)

    sim.run()

    labels = [label for label, _ in trace]
    assert labels == ["prio", "alpha", "beta", "delta"]
    times = [ts for _, ts in trace]
    assert times[0] == pytest.approx(0.05)
    assert times[-1] == pytest.approx(0.1)
