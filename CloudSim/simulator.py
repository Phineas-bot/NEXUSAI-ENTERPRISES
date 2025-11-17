from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple
import heapq
import itertools


@dataclass(order=True)
class _ScheduledEvent:
    scheduled_time: float
    priority: int
    order: int
    callback: Callable[..., None] = field(compare=False)
    args: Tuple[Any, ...] = field(default_factory=tuple, compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)


class Simulator:
    """Discrete-event simulator with deterministic ordering."""

    def __init__(self, start_time: float = 0.0):
        self._clock = start_time
        self._queue: List[_ScheduledEvent] = []
        self._order_counter = itertools.count()
        self._running = False

    @property
    def now(self) -> float:
        """Return the current simulated time."""
        return self._clock

    def schedule_at(
        self,
        scheduled_time: float,
        callback: Callable[..., None],
        *args: Any,
        priority: int = 0,
        **kwargs: Any,
    ) -> None:
        """Schedule a callback to run at an absolute simulated time."""
        if scheduled_time < self._clock:
            raise ValueError("Cannot schedule events in the past")

        event = _ScheduledEvent(
            scheduled_time=scheduled_time,
            priority=priority,
            order=next(self._order_counter),
            callback=callback,
            args=args,
            kwargs=kwargs,
        )
        heapq.heappush(self._queue, event)

    def schedule_in(
        self,
        delay: float,
        callback: Callable[..., None],
        *args: Any,
        priority: int = 0,
        **kwargs: Any,
    ) -> None:
        """Schedule a callback relative to the current simulated time."""
        if delay < 0:
            raise ValueError("Delay must be non-negative")
        self.schedule_at(self._clock + delay, callback, *args, priority=priority, **kwargs)

    def run(self, until: Optional[float] = None, max_events: Optional[int] = None) -> None:
        """Run the simulation until the queue empties or a limit is hit."""
        processed = 0
        self._running = True

        while self._queue and self._running:
            event = heapq.heappop(self._queue)
            if until is not None and event.scheduled_time > until:
                heapq.heappush(self._queue, event)
                break

            self._clock = event.scheduled_time
            event.callback(*event.args, **event.kwargs)

            processed += 1
            if max_events is not None and processed >= max_events:
                break

        self._running = False

    def stop(self) -> None:
        """Stop processing after the current event."""
        self._running = False

    def clear(self) -> None:
        """Remove all scheduled events."""
        self._queue.clear()
