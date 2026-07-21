"""Hybrid Logical Clock — Demirbas, Leone, Avva, Madeppa & Kulkarni, 2014.

Implements the HLC scheme used by CockroachDB and MongoDB for consistent
snapshots. Each timestamp is a pair (l, c): a physical-time component l
and a logical counter c. The only correctness property: if event A
happens-before event B, A's HLC timestamp must be strictly less than B's
— even if A and B occurred on machines with skewed physical clocks.

The merge rules below are the standard, well-known algorithm — not
something to redesign.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from backend.verifier.events import HLCTimestamp, Event


class HLCClock:
    """A mutable Hybrid Logical Clock instance.

    Each agent, transport adapter, and the event bus holds its own HLCClock.
    The clock is advanced on local/send events and merged on receive events,
    guaranteeing causal ordering even under physical-clock skew.
    """

    def __init__(self, physical_clock: callable = None):
        """Initialize with an optional injectable physical clock.

        Args:
            physical_clock: A callable returning the current physical time in
                milliseconds since epoch. Defaults to time.time_ns() // 1_000_000.
                Injectable for testing under simulated clock skew.
        """
        self._physical_clock = physical_clock or (lambda: time.time_ns() // 1_000_000)
        self._l: int = 0
        self._c: int = 0

    @property
    def current(self) -> HLCTimestamp:
        """Return the current HLC reading without advancing."""
        return HLCTimestamp(l=self._l, c=self._c)

    def now(self) -> HLCTimestamp:
        """Convenience: advance on a local event and return the new timestamp."""
        return self.send_or_local_event()

    def send_or_local_event(self) -> HLCTimestamp:
        """Advance the clock for a local or send event.

        Standard HLC merge rule — Demirbas et al., 2014:
        - new_l = max(clock.l, physical_now)
        - new_c = clock.c + 1 if new_l == clock.l else 0

        Returns:
            The new HLC timestamp after advancing.
        """
        physical_now = self._physical_clock()
        new_l = max(self._l, physical_now)
        new_c = self._c + 1 if new_l == self._l else 0
        self._l = new_l
        self._c = new_c
        return HLCTimestamp(l=new_l, c=new_c)

    def on_receive(self, remote: HLCTimestamp) -> HLCTimestamp:
        """Merge the clock with a received remote timestamp.

        Standard HLC merge rule — Demirbas et al., 2014:
        - new_l = max(clock.l, remote.l, physical_now)
        - If all three l values are equal: new_c = max(clock.c, remote.c) + 1
        - If new_l == clock.l only: new_c = clock.c + 1
        - If new_l == remote.l only: new_c = remote.c + 1
        - Otherwise (physical_now dominated): new_c = 0

        Guarantees: the returned timestamp is strictly greater than both
        the local predecessor's timestamp and the remote message's timestamp.

        Returns:
            The new HLC timestamp after merging.
        """
        physical_now = self._physical_clock()
        new_l = max(self._l, remote.l, physical_now)

        if new_l == self._l == remote.l:
            new_c = max(self._c, remote.c) + 1
        elif new_l == self._l:
            new_c = self._c + 1
        elif new_l == remote.l:
            new_c = remote.c + 1
        else:
            new_c = 0

        self._l = new_l
        self._c = new_c
        return HLCTimestamp(l=new_l, c=new_c)


def reconstruct_causal_order(events: list[Event]) -> list[Event]:
    """Sort a (possibly shuffled/out-of-order) list of events by HLC timestamp.

    This produces an ordering that respects every known happens-before pair,
    because HLC guarantees: if A happens-before B, then A.hlc < B.hlc.

    The sort is stable: events with identical HLC timestamps preserve their
    relative input order (which is fine — identical HLC means concurrent,
    and any ordering of concurrent events is valid).

    Args:
        events: A list of Event objects, possibly out of causal order.

    Returns:
        A new list sorted by HLC timestamp (causal order).
    """
    return sorted(events, key=lambda e: (e.hlc.l, e.hlc.c))
