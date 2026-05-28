"""mesh.solve -- the deterministic cascade core.

solve is pure control flow over INJECTED ops, so these tests use fakes (no
hardware). The load-bearing test is `test_cap_is_never_exceeded`: with a gate
that always fails, the GPU is called exactly `repair_cap` times and never once
more -- the policy breach from the 2026-05-20 log review cannot recur.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from cascade import mesh, topologies
from cascade.mesh import Candidate, GateInfo, Ops, RouteInfo


def _enable_warn_prompt(monkeypatch):
    """Flip CONFIG.warn_prompt_enabled=True AND disable skip_repair_on_degen
    for the duration of one test. The PD-1 v2 finding REVERTed warn-prompt to
    default-off; tests that exercise the threading path must opt in. We also
    flip skip-repair OFF because (as of FINDINGS-pd1-v2-skip-repair) it's
    default-on and would short-circuit the repair loop before warn-prompt can
    thread reasons -- the two levers can't both fire on the same trial."""
    monkeypatch.setattr(
        mesh, "CONFIG",
        replace(mesh.CONFIG, warn_prompt_enabled=True,
                skip_repair_on_degen=False),
    )


def _disable_skip_repair(monkeypatch):
    """Force CONFIG.skip_repair_on_degen=False. Used by pre-skip-repair tests
    that need to exercise the GPU-repair-on-poisoned-prior path (today's
    behaviour pre-lever, still reachable via the env-var opt-out)."""
    monkeypatch.setattr(
        mesh, "CONFIG", replace(mesh.CONFIG, skip_repair_on_degen=False),
    )


def _enable_skip_repair_on_degen(monkeypatch, *, floor=0.30):
    """Flip CONFIG.skip_repair_on_degen=True for the duration of one test (or
    keep the default-on production value, but explicitly pinned so the test
    survives a future default flip-back). `floor` overrides the score
    threshold so tests that drive a borderline degen result can pin behaviour."""
    monkeypatch.setattr(
        mesh, "CONFIG",
        replace(mesh.CONFIG, skip_repair_on_degen=True,
                skip_repair_score_floor=floor),
    )


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

    def repair_prompt(q, _prior, _failures, _degen=()):
        counts["repair_prompt"] += 1
        # Stash the degen tuple so warn-prompt tests can assert what mesh.solve
        # threaded through.
        counts["_last_degen"] = tuple(_degen)
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


# ---- PD-1 v1 passive observer --------------------------------------------


def test_passive_observer_emits_degen_trace_per_candidate():
    """One `degen[<tier>]:` line per candidate produced. Tier token stays a
    clean key ("npu"/"igpu"/"gpu") so downstream parsers can split by the
    bracket contents; repair-round info travels in the line just above."""
    ops, _c = make_ops(gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    degen_lines = [line for line in out.trace if line.startswith("degen[")]
    assert len(degen_lines) == 2
    assert any(line.startswith("degen[npu]:") for line in degen_lines)
    assert any(line.startswith("degen[gpu]:") for line in degen_lines)
    # The repair-round prefix is the existing "gpu repair round N" line
    # immediately preceding the gpu degen observation.
    assert any("repair round 1" in line for line in out.trace)


def test_passive_observer_records_for_gpu_fresh_generate():
    """gpu_only topology -> first GPU call is fresh generate, observed as 'gpu'."""
    topo = topologies.Topology("gpu_only", ("gpu",))
    ops, _c = make_ops(gate_seq=[True])
    out = mesh.solve("q", topo, ops)
    assert any(line.startswith("degen[gpu]:") for line in out.trace)


def test_passive_observer_uses_tier_status_when_provided():
    """When ops.tier_status is wired, an unavailable tier shows up as a reason
    in every degen trace line (text might be clean but the tier signal trips)."""
    ops, _c = make_ops(gate_seq=[True])
    ops_with_status = mesh.Ops(
        route=ops.route, draft=ops.draft, generate=ops.generate,
        gate=ops.gate, repair_prompt=ops.repair_prompt,
        igpu_draft=ops.igpu_draft,
        tier_status=lambda: {"npu": True, "gpu": False},
    )
    out = mesh.solve("q", "balanced", ops_with_status)
    degen_lines = [line for line in out.trace if line.startswith("degen[")]
    assert degen_lines
    assert all("tier:gpu unavailable" in line for line in degen_lines)


def test_passive_observer_does_not_change_outcome():
    """v1 is telemetry-only: the observer never touches control flow. The
    outcome of a happy-path solve is identical with and without tier_status.
    Each solve gets its own ops bundle because make_ops's gate_seq is
    stateful (consumed on each gate call)."""
    ops_no, _ = make_ops(gate_seq=[True])
    out_no = mesh.solve("q", "balanced", ops_no)
    ops_yes_base, _ = make_ops(gate_seq=[True])
    ops_yes = mesh.Ops(
        route=ops_yes_base.route, draft=ops_yes_base.draft,
        generate=ops_yes_base.generate, gate=ops_yes_base.gate,
        repair_prompt=ops_yes_base.repair_prompt,
        tier_status=lambda: {"npu": False},        # tier down doesn't escalate
    )
    out_yes = mesh.solve("q", "balanced", ops_yes)
    assert out_no.final_tier == out_yes.final_tier
    assert out_no.resolved == out_yes.resolved
    assert out_no.answer == out_yes.answer


# ---- SD-2b: observe_emit side-channel ------------------------------------


def test_observe_emit_receives_tier_and_result_per_observation():
    """The SD-2b recorder sink is invoked once per draft/repair output, with
    the tier name and the full DegenerationResult (so the recorder doesn't
    have to re-parse the trace string)."""
    from cascade.degeneration import DegenerationResult
    sink: list[tuple[str, DegenerationResult]] = []
    ops_base, _ = make_ops(gate_seq=[False, True])  # npu fail, gpu repair pass
    ops = mesh.Ops(
        route=ops_base.route, draft=ops_base.draft, generate=ops_base.generate,
        gate=ops_base.gate, repair_prompt=ops_base.repair_prompt,
        observe_emit=lambda tier, result: sink.append((tier, result)),
    )
    mesh.solve("q", "balanced", ops)
    tiers = [t for t, _ in sink]
    assert tiers == ["npu", "gpu"]
    assert all(isinstance(r, DegenerationResult) for _, r in sink)


def test_observe_emit_none_is_a_silent_no_op():
    """The hook is OPTIONAL; tests and the in-process orchestrator may pass
    None and the cascade still runs to completion."""
    ops, _ = make_ops(gate_seq=[True])
    out = mesh.solve("q", "balanced", ops)  # observe_emit defaults to None
    assert out.resolved and out.final_tier == "npu"


# ---- PD-1 v2 warn-prompt action lever -------------------------------------


# A trigram-looping NPU draft -- "the cat sat" repeated trips trigram_repeat
# (Youden's J=0.90) AND ttr (lexical narrowing). Used to force `degraded=True`
# in tests that need a non-empty `prior_degen` to thread.
#
# THRESHOLD COUPLING: this fixture is calibrated against the v2 thresholds in
# `cascade/degeneration_thresholds.json` (trigram_repeat_max=0.1372,
# ttr_min=0.3181 as of #66). A v3 re-calibration that loosens these will
# require regenerating this string. If `test_warn_prompt_threads_text_reasons
# _into_repair` starts failing on `assert threaded` after a threshold bump,
# this is the line that needs updating, not the production code.
_LOOPING_DRAFT = "the cat sat on the mat. " * 8


def test_warn_prompt_threads_text_reasons_into_repair(monkeypatch):
    """When the NPU draft is degraded AND warn-prompt is enabled, mesh.solve
    must pass the text-only degeneration reasons as the 4th arg to
    ops.repair_prompt so the repair model knows what failure mode to avoid
    (PD-1 v2 warn-prompt lever)."""
    _enable_warn_prompt(monkeypatch)
    ops, c = make_ops(draft_text=_LOOPING_DRAFT, gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    # Degen reasons were captured and threaded -- "looping:" or "narrowing:"
    # depending on which v2 metric tripped; tier reasons must NOT appear.
    threaded = c["_last_degen"]
    assert threaded, "expected non-empty degen reasons threaded into repair"
    assert all(r.startswith(("looping:", "narrowing:")) for r in threaded)
    # And the trace records the warn-prompt action so the dashboard can count it.
    assert any(line.startswith("warn-prompt[round 1]:") for line in out.trace)


def test_warn_prompt_threads_empty_when_prior_was_clean():
    """If the prior draft was clean (no text metric tripped), prior_degen is
    `()` and the repair prompt is byte-identical to today's behaviour."""
    ops, c = make_ops(draft_text="def f(): return 1", gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    assert c["_last_degen"] == ()
    # No warn-prompt trace line emitted when there are no reasons to thread.
    assert not any(line.startswith("warn-prompt") for line in out.trace)


def test_warn_prompt_filters_tier_only_reasons(monkeypatch):
    """A tier-unavailability reason ('tier:gpu unavailable') is about cascade
    health, not the prior draft -- it must NOT show up in the warn-prompt.
    Clean draft text + a tier-down signal -> empty `prior_degen`."""
    _enable_warn_prompt(monkeypatch)
    ops_base, c = make_ops(draft_text="x = 1", gate_seq=[False, True])
    ops = mesh.Ops(
        route=ops_base.route, draft=ops_base.draft, generate=ops_base.generate,
        gate=ops_base.gate, repair_prompt=ops_base.repair_prompt,
        igpu_draft=ops_base.igpu_draft,
        tier_status=lambda: {"npu": True, "gpu": False},
    )
    mesh.solve("q", "balanced", ops)
    assert c["_last_degen"] == ()


def test_warn_prompt_default_off_does_not_thread_even_when_degraded(monkeypatch):
    """Default-off path: even when the NPU draft is degraded (would otherwise
    populate prior_degen), the repair_prompt callsite must receive `()` and
    no warn-prompt trace line is emitted. Pinned by the PD-1 v2 REVERT
    (docs/FINDINGS-pd1-v2-warn-prompt.md). Skip-repair is also disabled here
    so the repair loop actually runs (skip-repair is default-on as of
    FINDINGS-pd1-v2-skip-repair and would otherwise short-circuit)."""
    # Do NOT enable warn_prompt -- exercise the default config. Disable
    # skip-repair so the GPU phase reaches the repair callsite this test pins.
    _disable_skip_repair(monkeypatch)
    ops, c = make_ops(draft_text=_LOOPING_DRAFT, gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    assert c["_last_degen"] == ()
    assert not any(line.startswith("warn-prompt") for line in out.trace)


# ---- PD-1 v2 skip-repair action lever -------------------------------------


def test_skip_repair_discards_poisoned_prior_and_does_fresh_gpu_generate(monkeypatch):
    """When CONFIG.skip_repair_on_degen is on AND the NPU draft observation
    scores >= the floor, mesh.solve DISCARDS the poisoned prior and routes the
    GPU phase into a fresh `generate` (not a repair). Distinct from the
    hard-escalate lever (which would skip GPU entirely). The experiment
    harness (scripts/skip_repair_validation.py) flips this on per arm."""
    _enable_skip_repair_on_degen(monkeypatch)
    # gate_seq: [draft FAIL, fresh GPU generate PASS] -- no repair call needed.
    ops, c = make_ops(draft_text=_LOOPING_DRAFT, gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.resolved
    # Zero repair rounds: GPU did a fresh generate (line 189 branch), not a
    # repair on the poisoned NPU prior.
    assert out.repair_rounds == 0
    assert c["draft"] == 1 and c["generate"] == 1 and c["repair_prompt"] == 0
    assert any(line.startswith("skip-repair:") for line in out.trace)
    assert any("discard prior" in line for line in out.trace)


def test_skip_repair_falls_back_to_repair_loop_if_fresh_gpu_also_fails(monkeypatch):
    """Once the prior is discarded, the cascade behaves byte-identically to a
    `gpu_only`-style entry: fresh generate first, then bounded repair if THAT
    fails. The skip-repair lever does not cap-and-handoff -- it just refuses
    to chain the bad NPU output into the repair prompt."""
    _enable_skip_repair_on_degen(monkeypatch)
    # gate_seq: [draft FAIL, fresh gen FAIL, repair#1 PASS]
    ops, c = make_ops(draft_text=_LOOPING_DRAFT, gate_seq=[False, False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    # The repair_prompt was called ONCE -- and on the GPU's own fresh output,
    # NOT on the discarded NPU prior. The 4th arg (degen reasons) is empty
    # because we discarded prior_degen too.
    assert c["repair_prompt"] == 1
    assert c["_last_degen"] == ()


def test_skip_repair_does_not_fire_on_clean_draft(monkeypatch):
    """A clean NPU draft (score < floor) must NOT trigger the lever even when
    enabled -- skip-repair is a quality gate, not an unconditional discard.
    The draft fails the gate for other reasons (boom), so we see the today
    behaviour: GPU repair on the (clean) NPU prior."""
    _enable_skip_repair_on_degen(monkeypatch)
    ops, c = make_ops(draft_text="def f(): return 1", gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    # Repair runs on the clean NPU prior (lever did NOT fire) -> generate is
    # the repair call, not a fresh generate.
    assert c["generate"] == 1 and c["repair_prompt"] == 1
    assert not any(line.startswith("skip-repair:") for line in out.trace)


def test_skip_repair_default_on_discards_poisoned_prior(monkeypatch):
    """Default-on path (production as of FINDINGS-pd1-v2-skip-repair): a
    degraded NPU draft is DISCARDED and the GPU phase issues a fresh
    `generate`. Pinned so a future regression that re-shadows the lever (env
    var, refactor, or default flip-back) doesn't silently change behaviour.
    Mirror of the warn-prompt default-off pin (PD-1 v2 precedent)."""
    # Do NOT touch CONFIG -- exercise the production default. Clear the env
    # opt-out in case the test process inherited CASCADE_SKIP_REPAIR_ON_DEGEN=0
    # from a shell rolling the lever back.
    monkeypatch.delenv("CASCADE_SKIP_REPAIR_ON_DEGEN", raising=False)
    ops, c = make_ops(draft_text=_LOOPING_DRAFT, gate_seq=[False, True])
    # Production default-on requires the same CONFIG construction the env-aware
    # tests use, since the module-level CONFIG was frozen at import (before
    # this monkeypatch). Rebuild from defaults to pick up env state.
    monkeypatch.setattr(mesh, "CONFIG", type(mesh.CONFIG)())
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.resolved
    assert out.repair_rounds == 0  # fresh generate, not repair on poison
    assert c["draft"] == 1 and c["generate"] == 1 and c["repair_prompt"] == 0
    assert any(line.startswith("skip-repair:") for line in out.trace)


def test_skip_repair_env_opt_out_restores_pre_lever_behaviour(monkeypatch):
    """CASCADE_SKIP_REPAIR_ON_DEGEN=0 disables the lever, returning to the
    pre-lever GPU-repair-on-poisoned-prior path. Lets production roll back
    without a code change."""
    monkeypatch.setenv("CASCADE_SKIP_REPAIR_ON_DEGEN", "0")
    monkeypatch.setattr(mesh, "CONFIG", type(mesh.CONFIG)())
    ops, c = make_ops(draft_text=_LOOPING_DRAFT, gate_seq=[False, True])
    out = mesh.solve("q", "balanced", ops)
    assert out.final_tier == "gpu" and out.repair_rounds == 1
    assert c["generate"] == 1 and c["repair_prompt"] == 1
    assert not any(line.startswith("skip-repair:") for line in out.trace)
