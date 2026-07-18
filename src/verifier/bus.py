"""In-memory event bus — the central point where every event gets HLC-stamped
and fanned out to consumers.

Regardless of which transport an event originates from, it passes through the
bus, gets an HLC timestamp (merged with the event's existing HLC from the
agent), and is distributed to all registered subscribers.

The bus also maintains per-resource event counters for lease TTL and
state-machine timeout tracking (Patch §3: per-resource, not global).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from src.verifier.events import Event, HLCTimestamp
from src.verifier.hlc import HLCClock

logger = logging.getLogger(__name__)

# Subscriber callback type: receives an Event, returns nothing
SubscriberCallback = Callable[[Event], Awaitable[None]]


class EventBus:
    """In-memory pub/sub event bus with HLC timestamping.

    All events from all transports funnel through here. The bus:
    1. Merges the event's HLC with its own clock (receive-side merge)
    2. Re-stamps the event with the merged timestamp
    3. Stores the event in the ordered log
    4. Fans the event out to all registered subscribers
    5. Increments per-resource event counters

    This is the single source of truth for causal ordering in the system.
    """

    def __init__(self, physical_clock: callable = None):
        """Initialize the event bus with its own HLC clock.

        Args:
            physical_clock: Injectable physical clock for testing.
        """
        self._clock = HLCClock(physical_clock=physical_clock)
        self._events: list[Event] = []
        self._subscribers: list[SubscriberCallback] = []
        self._resource_event_counts: dict[str, int] = {}  # per-resource counters
        self._global_event_count: int = 0

    @property
    def event_count(self) -> int:
        """Total events processed."""
        return self._global_event_count

    def get_resource_event_count(self, resource_id: str) -> int:
        """Get the per-resource event count for TTL/timeout tracking.

        Patch §3: lease TTL and after_events count per-resource, not global,
        so a chatty resource doesn't silently starve a quiet resource's
        timeout budget.
        """
        return self._resource_event_counts.get(resource_id, 0)

    def subscribe(self, callback: SubscriberCallback) -> None:
        """Register an async callback to receive every event.

        Subscribers are called in registration order for each event.
        A subscriber should not block — if it needs to do heavy work,
        it should enqueue internally.

        Args:
            callback: An async function taking an Event.
        """
        self._subscribers.append(callback)
        logger.debug("Subscriber registered (total: %d)", len(self._subscribers))

    async def emit(self, event: Event) -> Event:
        """Process and distribute an event.

        1. Merges the event's HLC with the bus clock (receive-side merge)
        2. Re-stamps the event with the merged timestamp
        3. Stores in the ordered log
        4. Increments per-resource event counter
        5. Fans out to all subscribers

        Args:
            event: The event to process. Its HLC will be merged/updated.

        Returns:
            The event with its updated (merged) HLC timestamp.
        """
        # Step 1 & 2: Merge clocks and re-stamp
        merged_hlc = self._clock.on_receive(event.hlc)
        event.hlc = merged_hlc

        # Step 3: Store
        self._events.append(event)
        self._global_event_count += 1

        # Step 4: Per-resource counter
        if event.resource_id:
            count = self._resource_event_counts.get(event.resource_id, 0) + 1
            self._resource_event_counts[event.resource_id] = count

        # Step 5: Fan out to subscribers
        for subscriber in self._subscribers:
            try:
                await subscriber(event)
            except Exception as e:
                logger.error("Subscriber error: %s", e, exc_info=True)

        logger.debug(
            "Event emitted: %s from %s via %s (HLC: %s)",
            event.kind, event.agent_id, event.transport, event.hlc,
        )
        return event

    def get_events(self) -> list[Event]:
        """Return all events in arrival order (which is causal order,
        since each event's HLC is merged on arrival)."""
        return list(self._events)

    def get_events_for_resource(self, resource_id: str) -> list[Event]:
        """Return all events for a specific resource in arrival order."""
        return [e for e in self._events if e.resource_id == resource_id]

    def reset(self) -> None:
        """Clear all state for re-runnability (Patch §8).

        run_demo.py should boot a fresh EventBus per invocation rather
        than resetting shared module-level state.
        """
        self._events.clear()
        self._subscribers.clear()
        self._resource_event_counts.clear()
        self._global_event_count = 0
        self._clock = HLCClock()
