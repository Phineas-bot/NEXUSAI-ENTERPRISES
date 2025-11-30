from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


class CloudSimStateStore:
    """Thin wrapper around a JSON snapshot on disk."""

    DEFAULT_FILENAME = "cloudsim_state.json"

    def __init__(self, path: Optional[str] = None) -> None:
        base = path or self.DEFAULT_FILENAME
        self.path = os.path.abspath(base)

    def load(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, payload: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, self.path)

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            return
        except OSError:
            return
