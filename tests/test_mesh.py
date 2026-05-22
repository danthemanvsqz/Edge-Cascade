"""mesh.solve -- the deterministic cascade core.

solve is pure control flow over INJECTED ops, so these tests use fakes (no
hardware). The load-bearing test is `test_cap_is_never_exceeded`: with a gate
that always fails, the GPU is called exactly `repair_cap` times and never once
more -- the policy breach from the 2026-05-20 log review cannot recur.
"""
from __future__ import annotations

from collections import Counter

import pytest

from cascade import mesh, topologies
from cascade.mesh import Candidate, GateInfo, Ops, RouteInfo


def make_ops(*, difficulty=0.5, category="standard", draft_text="DRAFT",
             gen_text="GEN", gate_seq=None, gen_available=True,
             igpu_text=None):
    """Build fake Ops + a call Counter. `gate_seq` is the ordered pass/fail of
    each gate call; once exhausted the gate fails (so an empty seq = always
    fails). Pass `igpu_text` to wire a Tier-1b iGPU drafter."""
    counts: Counter = Counter()
    seq = list(gate_seq or [])

    def route(_q):
        counts["route"] += 1
        return RouteInfo(difficulty, category)

    def draft(_q):
        counts["draft"] += 1
        return Candidate(draft_text)

    def generate(_q):
        counts["generate"] += 1
        return Candidate(gen_text, available=gen_available)

    def gate(_text):
        counts["gate"] += 1
        passed = seq.pop(0) if seq else False
        return GateInfo(passed, () if passed else ("boom",),
                        "" if passed else "gate fail")

    def repair_prompt(q, _prior, _failures):
        counts["repair_prompt"] += 1
        return f"REPAIR:{q}"

    igpu_draft = None
    if igpu_text is not None:
        def igpu_draft(_q):
            counts["igpu_draft"] += 1
            return Candidate(igpu_text)

    return Ops(route, draft, generate, gate, repair_prompt,
               igpu_draft=igpu_draft), counts


def test_balanced_npu_draft_passes_gate():
    ops, c = make_ops(gate_seq=[True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "npu" and out.resolved and not out.capped
    assert out.repair_rounds == 0
    assert c["draft"] == 1 and c["generate"] == 0 and c["repair_prompt"] == 0


def test_balanced_escalates_and_gpu_repair_passes():
    ops, c = make_ops(gate_seq=[False, True])  # npu fails, gpu repair #1 passes
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.resolved
    assert out.repair_rounds == 1
    assert c["generate"] == 1 and c["repair_prompt"] == 1


def test_cap_is_never_exceeded():
    # Gate always fails. cap is 2 -> exactly 2 GPU repair calls, then Tier-3.
    ops, c = make_ops(gate_seq=[])
    out = mesh.solve("q", "balanced", ops)
    cap = topologies.get("balanced").repair_cap
    assert out.final_tier == "capped->tier3"
    assert out.capped and not out.resolved and out.answer is None
    assert out.repair_rounds == cap
    # The breach guard: GPU generate + repair_prompt happen EXACTLY cap times.
    assert c["generate"] == cap == 2
    assert c["repair_prompt"] == cap == 2
    assert c["draft"] == 1  # the one initial NPU draft


def test_low_power_caps_immediately_on_npu_fail():
    ops, c = make_ops(gate_seq=[False])
    out = mesh.solve("q", "low_power", ops)
    assert out.final_tier == "capped->tier3" and out.repair_rounds == 0
    assert c["generate"] == 0  # GPU never spun


def test_low_power_npu_passes():
    ops, _c = make_ops(gate_seq=[True])
    out = mesh.solve("q", "low_power", ops)
    assert out.final_tier == "npu" and out.resolved


def test_gpu_only_fresh_generate_passes():
    topo = topologies.Topology("gpu_only", ("gpu",))
    ops, c = make_ops(gate_seq=[True])
    out = mesh.solve("q", topo, ops)  # Topology object, not a name
    assert out.final_tier == "gpu" and out.repair_rounds == 0
    assert c["draft"] == 0 and c["generate"] == 1


def test_gpu_only_fresh_generate_unavailable_caps():
    topo = topologies.Topology("gpu_only", ("gpu",))
    ops, c = make_ops(gen_available=False)
    out = mesh.solve("q", topo, ops)
    assert out.final_tier == "capped->tier3" and out.repair_rounds == 0
    assert c["generate"] == 1 and c["gate"] == 0


def test_gpu_only_fresh_fails_then_repair_passes():
    topo = topologies.Topology("gpu_only", ("gpu",))
    ops, c = make_ops(gate_seq=[False, True])  # fresh fails, repair #1 passes
    out = mesh.solve("q", topo, ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    assert c["generate"] == 2 and c["repair_prompt"] == 1


def test_skip_draft_above_skips_the_npu_draft():
    # Hard route -> skip the always-failing NPU draft, go straight to GPU.
    topo = topologies.Topology("hard", ("npu", "gpu"), skip_draft_above=0.7)
    ops, c = make_ops(difficulty=0.85, gate_seq=[True])
    out = mesh.solve("q", topo, ops)
    assert out.final_tier == "gpu" and c["draft"] == 0
    assert any("skipped" in line for line in out.trace)


def test_draft_not_skipped_below_threshold():
    topo = topologies.Topology("hard2", ("npu", "gpu"), skip_draft_above=0.9)
    ops, c = make_ops(difficulty=0.5, gate_seq=[True])
    out = mesh.solve("q", topo, ops)
    assert out.final_tier == "npu" and c["draft"] == 1


def test_gpu_unavailable_midway_through_repair_caps():
    ops, c = make_ops(gate_seq=[False], gen_available=False)
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "capped->tier3"
    assert c["generate"] == 1  # first repair call hit an unreachable GPU


def test_balanced_skips_npu_draft_on_hard_route():
    # difficulty >= balanced.skip_draft_above (0.70) -> no NPU draft, GPU first.
    ops, c = make_ops(difficulty=0.75, gate_seq=[True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and c["draft"] == 0 and c["generate"] == 1


def test_hard_task_goes_straight_to_gpu():
    ops, c = make_ops(gate_seq=[True])
    out = mesh.solve("q", "hard_task", ops)
    assert out.final_tier == "gpu" and c["draft"] == 0 and c["generate"] == 1


def test_igpu_assist_uses_igpu_drafter_when_present():
    ops, c = make_ops(igpu_text="IGPU DRAFT", gate_seq=[True])
    out = mesh.solve("q", "igpu_assist", ops)
    assert out.final_tier == "igpu" and out.repair_rounds == 0
    assert c["igpu_draft"] == 1 and c["draft"] == 0


def test_igpu_assist_falls_back_to_npu_when_no_igpu_wired():
    ops, c = make_ops(gate_seq=[True])  # no igpu_text -> ops.igpu_draft is None
    out = mesh.solve("q", "igpu_assist", ops)
    assert out.final_tier == "npu" and c["draft"] == 1 and c["igpu_draft"] == 0
    assert any("unavailable" in line for line in out.trace)


def test_igpu_draft_fails_then_gpu_repairs():
    ops, c = make_ops(igpu_text="IGPU", gate_seq=[False, True])
    out = mesh.solve("q", "igpu_assist", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    assert c["igpu_draft"] == 1 and c["generate"] == 1


def test_unknown_topology_name_raises():
    ops, _c = make_ops()
    with pytest.raises(KeyError):
        mesh.solve("q", "does-not-exist", ops)


def test_outcome_carries_route_and_topology_fields():
    ops, _c = make_ops(difficulty=0.42, category="standard", gate_seq=[True])
    out = mesh.solve("q", "balanced", ops)
    assert out.difficulty == 0.42 and out.topology == "balanced"
    assert out.trace[0].startswith("route difficulty=0.42")
