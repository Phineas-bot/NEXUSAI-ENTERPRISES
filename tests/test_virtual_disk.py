import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from virtual_disk import VirtualDisk  # noqa: E402


def test_reserve_and_commit_chunks_tracks_usage():
    disk = VirtualDisk(capacity_bytes=10 * 1024 * 1024)
    assert disk.reserve_file("file-a", 6 * 1024 * 1024)

    disk.write_chunk("file-a", 0, data=None, expected_size=4 * 1024 * 1024)
    assert disk.used_bytes == 4 * 1024 * 1024
    assert disk.reserved_bytes == 2 * 1024 * 1024

    disk.write_chunk("file-a", 1, data=None, expected_size=2 * 1024 * 1024)
    assert disk.used_bytes == 6 * 1024 * 1024
    assert disk.reserved_bytes == 0

    content = disk.read_file("file-a")
    assert len(content) == 6 * 1024 * 1024


def test_capacity_enforced_and_release_reclaims_space():
    disk = VirtualDisk(capacity_bytes=8 * 1024 * 1024)
    assert disk.reserve_file("base", 6 * 1024 * 1024)
    assert not disk.reserve_file("overflow", 3 * 1024 * 1024)

    disk.write_chunk("base", 0, data=b"a" * (2 * 1024 * 1024), expected_size=2 * 1024 * 1024)
    disk.release_file("base")

    assert disk.used_bytes == 0
    assert disk.reserved_bytes == 0
    assert disk.reserve_file("new", 8 * 1024 * 1024)
