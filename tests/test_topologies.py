"""Topology table: pure data + a loud lookup. Part of the S1 Celery seam."""
from __future__ import annotations

import pytest

from cascade import topologies
from cascade.config import CONFIG


def test_budget_is_the_default_cascade():
    t = topologies.get("budget")
    assert t.ladder == ("npu", "gpu")
    # repair_cap defaults to the single-source constant (charter inv. 4).
    assert t.repair_cap == CONFIG.repair_cap
    # budget skips the wasted NPU draft on hard tasks (S2, npu:0 finding).
    assert t.skip_draft_above == CONFIG.escalate_to_gpu_difficulty


def test_low_power_is_npu_only_with_no_repair():
    t = topologies.get("low_power")
    assert t.ladder == ("npu",)
    assert t.repair_cap == 0


def test_hard_task_is_gpu_only():
    t = topologies.get("hard_task")
    assert t.ladder == ("gpu",)  # skips Tier-1 entirely
    assert t.repair_cap == CONFIG.repair_cap


def test_igpu_assist_drafts_on_igpu_then_gpu():
    t = topologies.get("igpu_assist")
    assert t.ladder == ("igpu", "gpu")


def test_default_topology_name_resolves():
    assert topologies.DEFAULT_TOPOLOGY in topologies.TOPOLOGIES


def test_unknown_topology_raises_with_valid_list():
    with pytest.raises(KeyError) as e:
        topologies.get("nope")
    msg = str(e.value)
    assert "nope" in msg and "budget" in msg and "low_power" in msg


class TestShouldSkipDraft:
    """The length-aware skip-draft decision (BACKLOG #8): skip the cheap NPU
    draft only for a task that is BOTH hard AND long."""

    THRESH = 0.70
    MIN = 240

    def test_skips_when_hard_and_long(self):
        long_q = "x" * 300
        assert topologies.should_skip_draft(0.85, long_q, self.THRESH, self.MIN) is True

    def test_does_not_skip_a_short_hard_prompt(self):
        # The fix: an over-rated one-liner gets the NPU shot instead of skipping.
        short_q = "implement a red-black tree with insert and delete"
        assert topologies.should_skip_draft(0.85, short_q, self.THRESH, self.MIN) is False

    def test_does_not_skip_below_the_difficulty_threshold(self):
        assert topologies.should_skip_draft(0.65, "x" * 300, self.THRESH, self.MIN) is False

    def test_never_skips_when_threshold_is_none(self):
        assert topologies.should_skip_draft(0.99, "x" * 300, None, self.MIN) is False

    def test_boundary_lengths(self):
        assert topologies.should_skip_draft(0.85, "x" * 240, self.THRESH, self.MIN) is True
        assert topologies.should_skip_draft(0.85, "x" * 239, self.THRESH, self.MIN) is False
