import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from controller import CloudSimController
from storage_virtual_network import DemandScalingConfig
from storage_virtual_node import TransferStatus


class PushFileWorkflowTests(unittest.TestCase):
    FILE_NAME = "ingest.bin"

    def setUp(self) -> None:
        self.controller = CloudSimController()
        self.controller._setup_runtime(DemandScalingConfig(enabled=False))
        self._build_topology()

    def _build_topology(self) -> None:
        ctl = self.controller
        ctl.add_node("uploader", storage_gb=10, bandwidth_mbps=800)
        ctl.add_node("storage-a", storage_gb=200, bandwidth_mbps=1200)
        ctl.add_node("storage-b", storage_gb=200, bandwidth_mbps=1200)
        ctl.connect_nodes("uploader", "storage-a", bandwidth_mbps=1000, latency_ms=2.0)
        ctl.connect_nodes("storage-a", "storage-b", bandwidth_mbps=1000, latency_ms=1.0)

    def test_push_file_auto_selects_remote_target(self) -> None:
        target_id, transfer = self.controller.push_file("uploader", self.FILE_NAME, 8 * 1024 * 1024)
        self.assertIn(target_id, {"storage-a", "storage-b"})
        self.assertIn(transfer.status, {TransferStatus.PENDING, TransferStatus.IN_PROGRESS})

        self.controller.run_until_idle()

        chosen_node = self.controller.network.nodes[target_id]
        stored_names = [t.file_name for t in chosen_node.stored_files.values()]
        self.assertIn(self.FILE_NAME, stored_names)
        uploader_files = [t.file_name for t in self.controller.network.nodes["uploader"].stored_files.values()]
        self.assertNotIn(self.FILE_NAME, uploader_files)

    def test_push_file_can_pin_locally(self) -> None:
        target_id, transfer = self.controller.push_file(
            "uploader",
            "local.bin",
            4 * 1024 * 1024,
            prefer_local=True,
        )
        self.assertEqual(target_id, "uploader")
        self.assertEqual(transfer.status, TransferStatus.COMPLETED)

        node = self.controller.network.nodes["uploader"]
        stored_names = [t.file_name for t in node.stored_files.values()]
        self.assertIn("local.bin", stored_names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
