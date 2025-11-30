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


def test_add_node_assigns_zone_when_missing():
    controller = CloudSimController()
    node = controller.add_node("node-auto")
    assert node.zone is not None
    info = controller.get_node_info("node-auto")
    assert info["zone"] == node.zone


def test_add_node_respects_zone_override():
    controller = CloudSimController()
    controller.add_node("node-eu", zone="eu-central-1a")
    info = controller.get_node_info("node-eu")
    assert info["zone"] == "eu-central-1a"


def test_parse_size_handles_suffixes():
    assert parse_size("1GB") == 1024 * 1024 * 1024
    assert parse_size("2mb") == 2 * 1024 * 1024
    assert parse_size("512kb") == 512 * 1024
    assert parse_size("100") == 100


def test_controller_persists_state(tmp_path):
    state_path = tmp_path / "state.json"
    controller = CloudSimController(enable_persistence=False)
    controller.add_node("persist-a", storage_gb=200)
    controller.add_node("persist-b", storage_gb=200)
    controller.connect_nodes("persist-a", "persist-b", bandwidth_mbps=500, latency_ms=1.0)
    transfer = controller.initiate_transfer("persist-a", "persist-b", "persist.bin", 5 * 1024 * 1024)
    controller.run_until_idle()
    assert transfer.completed_at is not None

    saved_path = controller.save_snapshot(str(state_path))
    assert state_path.exists() and str(state_path) == saved_path

    controller.reset_state()
    assert controller.get_node_info("persist-b") is None

    assert controller.load_snapshot(str(state_path))
    info = controller.get_node_info("persist-b")
    assert info is not None
    stored_files = info.get("stored_files", [])
    assert any(entry["file_name"] == "persist.bin" for entry in stored_files)
