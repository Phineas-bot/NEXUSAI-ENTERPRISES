"""Observability scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List
from datetime import datetime, timezone

from .config import ObservabilityConfig
from .models import ObservabilityEvent


@dataclass
class TelemetryCollector:
    config: ObservabilityConfig
    metrics: List[Dict[str, float]] = field(default_factory=list)
    events: List[ObservabilityEvent] = field(default_factory=list)

    def emit_metric(self, name: str, value: float, labels: Dict[str, str] | None = None) -> None:
        payload = {
            "name": name,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(labels or {}),
        }
        self.metrics.append(payload)

    def emit_event(self, message: str, attributes: Dict[str, str] | None = None) -> None:
        self.events.append(ObservabilityEvent(event_type="custom", message=message, attributes=attributes))

    def flush(self) -> None:
        # Placeholder for pushing metrics/logs to Prometheus/OTLP exporters
        self.metrics.clear()
        self.events.clear()
