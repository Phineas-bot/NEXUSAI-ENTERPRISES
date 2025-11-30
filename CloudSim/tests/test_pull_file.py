import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from controller import CloudSimController
from storage_virtual_network import DemandScalingConfig
from storage_virtual_node import TransferStatus


class PullFileWorkflowTests(unittest.TestCase):
    FILE_NAME = "dataset.bin"

    def setUp(self) -> None:
        self.controller = CloudSimController()
        # Disable auto-scaling/replica spawning to keep tests deterministic.
        self.controller._setup_runtime(DemandScalingConfig(enabled=False))
        self._bootstrap_topology()

    def _bootstrap_topology(self) -> None:
        controller = self.controller
        controller.add_node("client", storage_gb=50, bandwidth_mbps=800)
        controller.add_node("seed", storage_gb=50, bandwidth_mbps=800)
        controller.add_node("edge", storage_gb=50, bandwidth_mbps=800)
        controller.connect_nodes("client", "seed", bandwidth_mbps=1000, latency_ms=1.0)
        controller.connect_nodes("seed", "edge", bandwidth_mbps=1000, latency_ms=1.0)

        controller.initiate_transfer("client", "seed", self.FILE_NAME, 8 * 1024 * 1024)
        controller.run_until_idle()

        seed_files = [t.file_name for t in controller.network.nodes["seed"].stored_files.values()]
        self.assertIn(self.FILE_NAME, seed_files)

    def test_pull_file_creates_replica_when_missing(self) -> None:
        pull_transfer = self.controller.pull_file("edge", self.FILE_NAME)
        self.assertTrue(pull_transfer.is_retrieval)

        self.controller.run_until_idle()

        edge_node = self.controller.network.nodes["edge"]
        stored_names = [t.file_name for t in edge_node.stored_files.values()]
        self.assertIn(self.FILE_NAME, stored_names)

    def test_pull_file_returns_existing_copy_without_retransfer(self) -> None:
        # Prime the node via a replica fetch first.
        self.controller.pull_file("edge", self.FILE_NAME)
        self.controller.run_until_idle()
        edge_node = self.controller.network.nodes["edge"]
        existing_ids = set(edge_node.stored_files.keys())
        seed_ops_before = len(self.controller.network.transfer_operations.get("seed", {}))
        self.assertEqual(seed_ops_before, 0)

        second_request = self.controller.pull_file("edge", self.FILE_NAME)
        self.assertEqual(second_request.status, TransferStatus.COMPLETED)
        self.assertIn(second_request.file_id, existing_ids)

        # No new transfer should have been initiated because the data was already local.
        seed_ops_after = len(self.controller.network.transfer_operations.get("seed", {}))
        self.assertEqual(seed_ops_after, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
