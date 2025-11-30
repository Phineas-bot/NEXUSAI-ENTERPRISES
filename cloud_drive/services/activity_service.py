"""Activity and notification scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..messaging import InMemoryBus, MessageEnvelope
from ..telemetry import TelemetryCollector


@dataclass
class ActivityService:
    bus: InMemoryBus
    telemetry: TelemetryCollector
    events: List[MessageEnvelope] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.bus.subscribe("ingest.requests", self._handle_event)
        self.bus.subscribe("replication.requests", self._handle_event)
        self.bus.subscribe("healing.events", self._handle_event)
        self.bus.subscribe("lifecycle.transitions", self._handle_event)

    def _handle_event(self, envelope: MessageEnvelope) -> None:
        self.events.append(envelope)
        self.telemetry.emit_event(f"activity_{envelope.topic}", envelope.payload)
