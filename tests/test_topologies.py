"""Topology table: pure data + a loud lookup. Part of the S1 Celery seam."""
from __future__ import annotations

import pytest

from cascade import topologies
from cascade.config import CONFIG


def test_balanced_is_the_default_cascade():
    t = topologies.get("balanced")
    assert t.ladder == ("npu", "gpu")
    # repair_cap defaults to the single-source constant (charter inv. 4).
    assert t.repair_cap == CONFIG.repair_cap
    # balanced skips the wasted NPU draft on hard tasks (S2, npu:0 finding).
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
    assert "nope" in msg and "balanced" in msg and "low_power" in msg
