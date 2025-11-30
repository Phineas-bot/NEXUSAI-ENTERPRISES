"""Helper module to import CloudSim components regardless of sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

_CLOUDSIM_PATH = Path(__file__).resolve().parent.parent / "CloudSim"
path_str = str(_CLOUDSIM_PATH)
if _CLOUDSIM_PATH.exists() and path_str not in sys.path:
    sys.path.insert(0, path_str)

from controller import CloudSimController  # type: ignore  # noqa: E402
