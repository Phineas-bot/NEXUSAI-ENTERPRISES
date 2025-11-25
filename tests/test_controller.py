import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from controller import CloudSimController, parse_size


def test_add_and_list_nodes():
    controller = CloudSimController()
    controller.add_node("node-a", storage_gb=100)
    controller.add_node("node-b", storage_gb=200)
    controller.connect_nodes("node-a", "node-b")

    rows = controller.list_node_status()
    ids = {row.node_id for row in rows}
    assert ids == {"node-a", "node-b"}


def test_parse_size_handles_suffixes():
    assert parse_size("1GB") == 1024 * 1024 * 1024
    assert parse_size("2mb") == 2 * 1024 * 1024
    assert parse_size("512kb") == 512 * 1024
    assert parse_size("100") == 100
