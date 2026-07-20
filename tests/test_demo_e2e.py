"""End-to-end test — runs all three acts and asserts expected outcomes.

This is the single most convincing test — treat it as the thing you'd run
first if a judge says "prove it." (Section 10)

Tests:
- Act 1: clean run, zero violations, zero cycles
- Act 2: stale write rejected, valid write accepted
- Act 3: cycle detected, tokens revoked, tool calls rejected
"""

import asyncio
import sys
import os

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_demo import DemoOrchestrator


class TestDemoE2E:
    """End-to-end test running all three acts programmatically."""

    @pytest.fixture
    def orchestrator(self):
        """Fresh orchestrator for each test (Patch §8: re-runnability)."""
        return DemoOrchestrator()

    @pytest.mark.asyncio
    async def test_act1_happy_path(self, orchestrator):
        """Act 1: clean run, Acked state, zero violations, zero cycles."""
        results = await orchestrator.run_act1()

        assert results["final_state"] == "Acked", (
            f"Expected Acked, got {results['final_state']}"
        )
        assert results["violations"] == 0, (
            f"Expected 0 violations, got {results['violations']}"
        )
        assert results["cycles"] == 0, (
            f"Expected 0 cycles, got {results['cycles']}"
        )
        assert results["write_accepted"] is True

    @pytest.mark.asyncio
    async def test_act2_fencing_rejection(self, orchestrator):
        """Act 2: stale write rejected, valid write accepted, no double execution."""
        results = await orchestrator.run_act2()

        assert results["agent_a_write_rejected"] is True, (
            "Agent A's stale write should have been REJECTED"
        )
        assert results["agent_b_write_accepted"] is True, (
            "Agent B's valid write should have been ACCEPTED"
        )
        assert results["writes_in_log"] == 1, (
            f"Expected exactly 1 write in log, got {results['writes_in_log']}"
        )
        assert results["stale_token"] < results["valid_token"], (
            "Stale token should be less than valid token"
        )

    @pytest.mark.asyncio
    async def test_act3_cycle_detection(self, orchestrator):
        """Act 3: cycle detected, tokens revoked, tool calls rejected."""
        results = await orchestrator.run_act3()

        assert results["cycles_detected"] >= 1, (
            "Expected at least 1 dependency cycle"
        )
        assert results["agent_a_tokens_revoked"] is True, (
            "Agent A's tokens should be revoked"
        )
        assert results["agent_c_tokens_revoked"] is True, (
            "Agent C's tokens should be revoked"
        )
        assert results["agent_a_tool_rejected"] is True, (
            "Agent A's tool call should be rejected after revocation"
        )
        assert results["agent_c_tool_rejected"] is True, (
            "Agent C's tool call should be rejected after revocation"
        )

    @pytest.mark.asyncio
    async def test_full_demo_all_acts(self, orchestrator):
        """Running all three acts back to back produces expected outcomes:
        one clean run, one rejected write, one revoked pair.

        This is THE convincing test — Section 10.
        """
        results = await orchestrator.run_full_demo()

        # Act 1: Clean
        assert results["act1"]["final_state"] == "Acked"
        assert results["act1"]["violations"] == 0
        assert results["act1"]["cycles"] == 0

        # Act 2: Rejected
        assert results["act2"]["agent_a_write_rejected"] is True
        assert results["act2"]["agent_b_write_accepted"] is True

        # Act 3: Revoked
        assert results["act3"]["cycles_detected"] >= 1
        assert results["act3"]["agent_a_tool_rejected"] is True
        assert results["act3"]["agent_c_tool_rejected"] is True

    @pytest.mark.asyncio
    async def test_demo_re_runnability(self):
        """Running the demo twice in a row produces the same results.

        Patch §8: Fresh instances per invocation ensure re-runnability.
        """
        for run_number in range(2):
            orchestrator = DemoOrchestrator()
            results = await orchestrator.run_full_demo()

            assert results["act1"]["final_state"] == "Acked", (
                f"Run {run_number + 1}: Act 1 failed"
            )
            assert results["act2"]["agent_a_write_rejected"] is True, (
                f"Run {run_number + 1}: Act 2 failed"
            )
            assert results["act3"]["cycles_detected"] >= 1, (
                f"Run {run_number + 1}: Act 3 failed"
            )
