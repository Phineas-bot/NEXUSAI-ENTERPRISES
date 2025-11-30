"""Simple message bus abstractions for scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, DefaultDict, Dict, List
from collections import defaultdict


@dataclass
class MessageEnvelope:
    topic: str
    payload: Dict[str, Any]
    retries: int = 0


class InMemoryBus:
    """Naive pub/sub bus for local development and unit tests."""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[Callable[[MessageEnvelope], None]]] = defaultdict(list)

    def publish(self, envelope: MessageEnvelope) -> None:
        for callback in list(self._subscribers[envelope.topic]):
            callback(envelope)

    def subscribe(self, topic: str, handler: Callable[[MessageEnvelope], None]) -> None:
        self._subscribers[topic].append(handler)


def build_bus(backend: str = "in-memory") -> InMemoryBus:
    if backend != "in-memory":
        raise NotImplementedError("Only in-memory backend is scaffolded right now")
    return InMemoryBus()
