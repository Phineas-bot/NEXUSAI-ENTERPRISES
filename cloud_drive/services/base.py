"""Base class for control-plane services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import CloudDriveConfig
from ..telemetry import TelemetryCollector


@dataclass
class BaseService:
    config: CloudDriveConfig
    telemetry: TelemetryCollector

    def emit_metric(self, name: str, value: float, **labels: str) -> None:
        self.telemetry.emit_metric(name, value, labels)

    def emit_event(self, message: str, **attrs: str) -> None:
        self.telemetry.emit_event(message, attrs)
