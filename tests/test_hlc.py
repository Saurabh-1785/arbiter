"""Tests for the Hybrid Logical Clock implementation (Section 10).

Tests cover:
- Monotonicity under simulated clock skew
- Correct merge on receive (merged timestamp always dominates both inputs)
- Merge under injected clock skew (physical clock behind)
- Shuffle-and-reconstruct: shuffled events re-sorted by HLC still respect
  every known happens-before pair
"""

import random
from backend.verifier.events import HLCTimestamp, Event
from backend.verifier.hlc import HLCClock, reconstruct_causal_order


class TestHLCMonotonicity:
    """HLC timestamps must be monotonically increasing on local events."""

    def test_local_events_increase(self):
        """Sequential local events produce strictly increasing timestamps."""
        clock = HLCClock(physical_clock=lambda: 1000)
        ts1 = clock.send_or_local_event()
        ts2 = clock.send_or_local_event()
        ts3 = clock.send_or_local_event()
        assert ts1 < ts2 < ts3

    def test_monotonic_even_with_stale_physical_clock(self):
        """HLC stays monotonic even when the physical clock doesn't advance.

        This is the key property — if the physical clock is stale (returns
        the same value repeatedly), the logical counter c ensures monotonicity.
        """
        # Physical clock stuck at 1000
        clock = HLCClock(physical_clock=lambda: 1000)
        timestamps = [clock.send_or_local_event() for _ in range(10)]

        for i in range(len(timestamps) - 1):
            assert timestamps[i] < timestamps[i + 1], (
                f"Timestamps not monotonic: {timestamps[i]} >= {timestamps[i+1]}"
            )

        # All should have l=1000, with c incrementing
        for i, ts in enumerate(timestamps):
            assert ts.l == 1000
            assert ts.c == i

    def test_physical_clock_advancing_resets_counter(self):
        """When physical clock advances, logical counter resets to 0."""
        time_values = iter([1000, 1001, 1002])
        clock = HLCClock(physical_clock=lambda: next(time_values))

        ts1 = clock.send_or_local_event()
        ts2 = clock.send_or_local_event()
        ts3 = clock.send_or_local_event()

        assert ts1.l == 1000 and ts1.c == 0
        assert ts2.l == 1001 and ts2.c == 0
        assert ts3.l == 1002 and ts3.c == 0
        assert ts1 < ts2 < ts3

    def test_monotonic_under_clock_skew_backward(self):
        """If physical clock goes BACKWARD, HLC still increases.

        Simulates NTP correction pushing the clock back — HLC must not
        go backward even though the physical time did.
        """
        # Clock goes: 1000, 999, 998 (backward!)
        time_values = iter([1000, 999, 998])
        clock = HLCClock(physical_clock=lambda: next(time_values))

        ts1 = clock.send_or_local_event()
        ts2 = clock.send_or_local_event()
        ts3 = clock.send_or_local_event()

        assert ts1 < ts2 < ts3
        # l should stay at 1000 (max of clock.l and physical_now)
        assert ts1.l == 1000
        assert ts2.l == 1000
        assert ts3.l == 1000
        # c should increment since l didn't advance
        assert ts2.c == ts1.c + 1
        assert ts3.c == ts2.c + 1


class TestHLCMergeOnReceive:
    """on_receive() must produce a timestamp greater than both inputs."""

    def test_merge_dominates_both(self):
        """Merged timestamp is strictly greater than both local and remote."""
        clock = HLCClock(physical_clock=lambda: 1000)
        local_ts = clock.send_or_local_event()  # (1000, 0)

        remote_ts = HLCTimestamp(l=1000, c=5)  # remote is ahead in c
        merged = clock.on_receive(remote_ts)

        assert merged > local_ts, f"Merged {merged} not > local {local_ts}"
        assert merged > remote_ts, f"Merged {merged} not > remote {remote_ts}"

    def test_merge_remote_ahead_in_l(self):
        """Remote has higher physical time — merged should use remote's l."""
        clock = HLCClock(physical_clock=lambda: 1000)
        clock.send_or_local_event()  # advance to (1000, 0)

        remote_ts = HLCTimestamp(l=2000, c=3)
        merged = clock.on_receive(remote_ts)

        assert merged.l == 2000
        assert merged.c == 4  # remote.c + 1 since new_l == remote.l
        assert merged > remote_ts

    def test_merge_local_ahead_in_l(self):
        """Local has higher physical time — merged should use local's l."""
        # Start clock at 2000
        clock = HLCClock(physical_clock=lambda: 2000)
        clock.send_or_local_event()  # (2000, 0)

        remote_ts = HLCTimestamp(l=1000, c=10)
        merged = clock.on_receive(remote_ts)

        assert merged.l == 2000
        assert merged.c == 1  # local.c + 1 since new_l == local.l but != remote.l
        assert merged > remote_ts

    def test_merge_physical_dominates_both(self):
        """Physical clock is ahead of both local and remote — c resets to 0."""
        clock = HLCClock(physical_clock=lambda: 3000)
        clock._l = 1000  # artificially set low
        clock._c = 5

        remote_ts = HLCTimestamp(l=2000, c=10)
        merged = clock.on_receive(remote_ts)

        assert merged.l == 3000
        assert merged.c == 0  # physical dominated, so c resets
        assert merged > remote_ts

    def test_merge_all_equal_l(self):
        """All three l values equal — c = max(local.c, remote.c) + 1."""
        clock = HLCClock(physical_clock=lambda: 1000)
        clock._l = 1000
        clock._c = 3

        remote_ts = HLCTimestamp(l=1000, c=7)
        merged = clock.on_receive(remote_ts)

        assert merged.l == 1000
        assert merged.c == 8  # max(3, 7) + 1 = 8
        assert merged > remote_ts
        assert merged > HLCTimestamp(l=1000, c=3)

    def test_merge_under_injected_clock_skew(self):
        """Simulated clock skew: two clocks with different physical times.

        Clock A thinks it's time 1000, Clock B thinks it's time 1200.
        Messages between them should produce correct causal ordering
        despite the 200ms skew.
        """
        clock_a = HLCClock(physical_clock=lambda: 1000)
        clock_b = HLCClock(physical_clock=lambda: 1200)

        # A sends a message
        ts_a = clock_a.send_or_local_event()  # (1000, 0)

        # B receives it
        ts_b_after_receive = clock_b.on_receive(ts_a)  # (1200, 0) — B's physical dominates

        # B sends a reply
        ts_b_reply = clock_b.send_or_local_event()  # (1200, 1)

        # A receives the reply
        ts_a_after_receive = clock_a.on_receive(ts_b_reply)  # (1200, 2)

        # Causal chain: ts_a -> ts_b_after_receive -> ts_b_reply -> ts_a_after_receive
        assert ts_a < ts_b_after_receive
        assert ts_b_after_receive < ts_b_reply or ts_b_after_receive == ts_b_reply
        assert ts_b_reply < ts_a_after_receive
        assert ts_a < ts_a_after_receive  # transitive


class TestReconstructCausalOrder:
    """Shuffled events re-sorted by HLC must respect happens-before pairs."""

    def test_shuffle_and_reconstruct(self):
        """A shuffled event list, when sorted by HLC, still respects
        every known happens-before pair.

        This is the core property of HLC: if A happens-before B,
        then A.hlc < B.hlc, so sorting by HLC recovers causal order.
        """
        clock_a = HLCClock(physical_clock=lambda: 1000)
        clock_b = HLCClock(physical_clock=lambda: 1000)

        events = []
        happens_before_pairs = []

        # A sends event 1
        ts_a1 = clock_a.send_or_local_event()
        e1 = Event.create(agent_id="A", transport="grpc", hlc=ts_a1, kind="task_claim",
                          resource_id="t1", payload={"fencing_token": 1, "op": "write"})
        events.append(e1)

        # B receives and responds (happens-before: e1 -> e2)
        clock_b.on_receive(ts_a1)
        ts_b1 = clock_b.send_or_local_event()
        e2 = Event.create(agent_id="B", transport="queue", hlc=ts_b1, kind="handoff_ack",
                          resource_id="t1", payload={"op": "write"})
        events.append(e2)
        happens_before_pairs.append((e1, e2))

        # A receives B's response and does more work (happens-before: e2 -> e3)
        clock_a.on_receive(ts_b1)
        ts_a2 = clock_a.send_or_local_event()
        e3 = Event.create(agent_id="A", transport="grpc", hlc=ts_a2, kind="task_release",
                          resource_id="t1", payload={"op": "write"})
        events.append(e3)
        happens_before_pairs.append((e2, e3))

        # B does concurrent work (not causally related to e3)
        ts_b2 = clock_b.send_or_local_event()
        e4 = Event.create(agent_id="B", transport="queue", hlc=ts_b2, kind="task_claim",
                          resource_id="t2", payload={"fencing_token": 1, "op": "write"})
        events.append(e4)

        # Shuffle many times and verify
        for _ in range(20):
            shuffled = events.copy()
            random.shuffle(shuffled)
            reconstructed = reconstruct_causal_order(shuffled)

            # Verify all happens-before pairs are respected
            for before, after in happens_before_pairs:
                idx_before = next(i for i, e in enumerate(reconstructed) if e.event_id == before.event_id)
                idx_after = next(i for i, e in enumerate(reconstructed) if e.event_id == after.event_id)
                assert idx_before < idx_after, (
                    f"Happens-before violated: {before.event_id} should be before {after.event_id} "
                    f"but was at index {idx_before} vs {idx_after}"
                )

    def test_reconstruct_preserves_concurrent_events(self):
        """Concurrent events (no happens-before) can appear in any order.

        The sort is stable, so concurrent events preserve input order,
        but either ordering is valid.
        """
        clock_a = HLCClock(physical_clock=lambda: 1000)
        clock_b = HLCClock(physical_clock=lambda: 1000)

        # Two independent events at the same physical time
        ts_a = clock_a.send_or_local_event()
        ts_b = clock_b.send_or_local_event()

        e1 = Event.create(agent_id="A", transport="grpc", hlc=ts_a, kind="task_claim",
                          resource_id="t1", payload={"fencing_token": 1, "op": "write"})
        e2 = Event.create(agent_id="B", transport="queue", hlc=ts_b, kind="task_claim",
                          resource_id="t2", payload={"fencing_token": 1, "op": "write"})

        # Both orderings are valid since they're concurrent
        reconstructed = reconstruct_causal_order([e2, e1])
        assert len(reconstructed) == 2
        # Both have (1000, 0) so order is stable — e2 was first in input
        assert reconstructed[0].event_id == e2.event_id


class TestHLCTimestampComparison:
    """Test all comparison operators on HLCTimestamp."""

    def test_less_than_by_l(self):
        assert HLCTimestamp(1000, 5) < HLCTimestamp(1001, 0)

    def test_less_than_by_c(self):
        assert HLCTimestamp(1000, 0) < HLCTimestamp(1000, 1)

    def test_equal(self):
        assert HLCTimestamp(1000, 5) == HLCTimestamp(1000, 5)

    def test_not_equal(self):
        assert HLCTimestamp(1000, 5) != HLCTimestamp(1000, 6)

    def test_greater_than(self):
        assert HLCTimestamp(1001, 0) > HLCTimestamp(1000, 99)

    def test_less_equal(self):
        assert HLCTimestamp(1000, 5) <= HLCTimestamp(1000, 5)
        assert HLCTimestamp(1000, 4) <= HLCTimestamp(1000, 5)

    def test_greater_equal(self):
        assert HLCTimestamp(1000, 5) >= HLCTimestamp(1000, 5)
        assert HLCTimestamp(1000, 6) >= HLCTimestamp(1000, 5)

    def test_hashable(self):
        """HLCTimestamp should be usable as dict keys and in sets."""
        ts1 = HLCTimestamp(1000, 0)
        ts2 = HLCTimestamp(1000, 0)
        s = {ts1, ts2}
        assert len(s) == 1

    def test_repr(self):
        assert repr(HLCTimestamp(1000, 5)) == "HLC(1000, 5)"
