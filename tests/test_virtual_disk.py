import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "CloudSim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from virtual_disk import DiskCorruptionError, DiskIOProfile, VirtualDisk  # noqa: E402


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


def test_async_scheduler_coordinates_completion_times():
    profile = DiskIOProfile(throughput_bytes_per_sec=10 * 1024 * 1024, seek_time_ms=5, max_outstanding=1)
    disk = VirtualDisk(capacity_bytes=64 * 1024 * 1024, io_profile=profile)
    assert disk.reserve_file("async", 8 * 1024 * 1024)

    ticket = disk.schedule_write("async", 0, expected_size=4 * 1024 * 1024, current_time=1.0)
    assert ticket.completion_time > 1.0

    disk.complete_write(ticket, data=b"b" * (4 * 1024 * 1024))
    assert disk.used_bytes == 4 * 1024 * 1024
    assert disk.read_chunk("async", 0)[:1] == b"b"


def test_filesystem_metadata_tracks_paths():
    disk = VirtualDisk(capacity_bytes=16 * 1024 * 1024)
    assert disk.reserve_file("meta", 4 * 1024 * 1024, path="/node-a/data/meta.bin")
    disk.write_chunk("meta", 0, data=b"x" * (4 * 1024 * 1024), expected_size=4 * 1024 * 1024)

    listing = disk.list_directory("/node-a/data")
    assert "meta.bin" in listing

    metadata = disk.get_file_metadata("meta")
    assert metadata["path"] == "/node-a/data/meta.bin"


def test_corruption_detection_and_recovery():
    disk = VirtualDisk(capacity_bytes=16 * 1024 * 1024)
    assert disk.reserve_file("integrity", 4 * 1024 * 1024)
    disk.write_chunk("integrity", 0, data=b"c" * (4 * 1024 * 1024), expected_size=4 * 1024 * 1024)

    disk.inject_corruption("integrity", 0)
    with pytest.raises(DiskCorruptionError):
        disk.read_chunk("integrity", 0)

    disk.recover_chunk("integrity", 0, repaired_data=b"d" * (4 * 1024 * 1024))
    assert disk.read_chunk("integrity", 0).startswith(b"d")
