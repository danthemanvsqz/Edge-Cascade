"""Transport-agnostic cascade orchestrator -- the deterministic core.

`solve(query, topology, ops)` runs the cascade as PURE CONTROL FLOW over
injected tier ops: route -> initial candidate -> bounded GPU repair loop ->
Tier-3 handoff. It performs no I/O and writes no `.rec` itself -- recording
stays at the op boundary (mcp_servers/_rec.py), and the ops are injected, so
this is unit-testable with fakes and equally drivable in-process today or over
Celery later (the ops become task signatures). See docs/CELERY-READINESS.md.

The repair-round cap is the load-bearing invariant: the loop is
`range(1, cap+1)`, so a (cap+1)'th round is structurally impossible. The policy
breach seen in the 2026-05-20 log review -- where the cap lived only as a
CLAUDE.md prompt rule and the agent ran a 3rd round -- cannot recur here.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cascade import topologies
from cascade.config import CONFIG
from cascade.degeneration import DegenerationResult, Thresholds, check_degeneration

_THRESHOLDS_PATH = Path(__file__).resolve().parent / "degeneration_thresholds.json"
# Load calibrated thresholds ONCE at module import (cheap stat + parse) rather
# than per-solve. Falls back to library defaults if the JSON is absent (fresh
# checkouts before calibration has run).
_THRESHOLDS = (Thresholds.load(_THRESHOLDS_PATH)
               if _THRESHOLDS_PATH.exists() else Thresholds())


@dataclass(frozen=True)
class RouteInfo:
    """Tier-1 routing verdict (only `difficulty` drives control flow)."""

    difficulty: float
    category: str = "standard"


@dataclass(frozen=True)
class Candidate:
    """A produced answer from a tier. `available` is False when the tier could
    not run (e.g. Ollama unreachable) -- the cascade treats that as a hand-off,
    not an answer."""

    text: str
    available: bool = True


@dataclass(frozen=True)
class GateInfo:
    """Verifier verdict for a candidate. `failures` feeds the repair prompt."""

    passed: bool
    failures: tuple = ()
    reason: str = ""


@dataclass(frozen=True)
class Ops:
    """The injected tier ops. Each respects the op boundary -- plain data in,
    plain data out -- so it lifts to a Celery task unchanged (charter inv. 1/2).
    `solve` calls only these; it never reaches into a worker or transport."""

    route: Callable[[str], RouteInfo]
    draft: Callable[[str], Candidate]                 # Tier-1 NPU draft (1.5B)
    generate: Callable[[str], Candidate]              # Tier-2 GPU generate
    gate: Callable[[str], GateInfo]                   # verify (syntax [+ functional])
    # PD-1 v2 warn-prompt: the 4th arg carries the prior draft's text-only
    # degeneration reasons (empty when the prior was clean). Threaded into the
    # repair prompt so the repair model knows what failure mode to avoid.
    # (query, prior, failures, degen) -> next query
    repair_prompt: Callable[[str, str, tuple, tuple], str]
    # Optional Tier-1b: a larger draft model on the Intel iGPU (3B). None when
    # no iGPU model is configured; a topology naming "igpu" then falls back to
    # the NPU draft. Default keeps existing Ops construction backward-compatible.
    igpu_draft: Callable[[str], Candidate] | None = None
    # Optional PD-1 input: snapshot of tier availability ({name: ok}). When None,
    # the degeneration detector observes text-only. Memoized by the wiring layer
    # so calling per observation site is cheap. Default keeps existing Ops
    # construction backward-compatible.
    tier_status: Callable[[], dict[str, bool]] | None = None
    # Optional PD-1 v1 sink: invoked for every observation with (tier_name,
    # DegenerationResult). Wiring binds this to a recorder writing the
    # `runs/cascade-degeneration.rec` lane so the SD-2b dashboard panel can
    # tail it without grepping cascade.log. Default None keeps mesh.solve
    # pure-functional in tests (no I/O required).
    observe_emit: Callable[[str, DegenerationResult], None] | None = None


@dataclass(frozen=True)
class Outcome:
    """Result of a solve. `capped` True means the locals are exhausted and
    Tier-3 (the launched Claude) must take over -- the single signal the agent
    acts on under model-B."""

    answer: str | None
    final_tier: str          # "npu" | "gpu" | "capped->tier3"
    resolved: bool
    capped: bool
    repair_rounds: int
    difficulty: float
    topology: str
    trace: tuple[str, ...]


def solve(query: str, topology: str | topologies.Topology, ops: Ops) -> Outcome:
    """Run the cascade for `query` under `topology` using the injected `ops`.

    Returns an `Outcome`: a verified answer attributed to the tier that produced
    it, or `capped` (Tier-3 takeover) when the NPU draft and the bounded GPU
    repair loop are exhausted. Accepts a topology name (looked up) or a Topology
    object (so callers/tests can pass an ad-hoc strategy)."""
    topo = (topology if isinstance(topology, topologies.Topology)
            else topologies.get(topology))
    trace: list[str] = []
    route = ops.route(query)
    trace.append(
        f"route difficulty={route.difficulty:.2f} category={route.category}")

    # PD-1 v1: passive observer. Thresholds loaded once at module import; tier
    # status snapshotted once (already memoized by wiring). TELEMETRY ONLY --
    # the verdict feeds a trace line, never control flow.
    tiers = ops.tier_status() if ops.tier_status is not None else None

    def observe(tier_name: str, text: str) -> DegenerationResult:
        d = check_degeneration(text, tier_availability=tiers, thresholds=_THRESHOLDS)
        trace.append(
            f"degen[{tier_name}]: score={d.score:.2f} reasons={list(d.reasons)}"
        )
        # Side-channel the verdict to the wired emitter (None in tests) so the
        # SD-2b dashboard panel can tail a dedicated `.rec` lane instead of
        # parsing trace strings. Mesh stays pure -- the I/O lives in the
        # injected callback, not here.
        if ops.observe_emit is not None:
            ops.observe_emit(tier_name, d)
        return d

    def capped(rounds: int) -> Outcome:
        trace.append("-> capped->tier3 (Tier-3 takes over)")
        return Outcome(None, "capped->tier3", False, True, rounds,
                       route.difficulty, topo.name, tuple(trace))

    def won(text: str, tier: str, rounds: int) -> Outcome:
        return Outcome(text, tier, True, False, rounds,
                       route.difficulty, topo.name, tuple(trace))

    # 1) Initial candidate: a Tier-1 draft. The drafter is the first draft-
    #    capable tier in the ladder -- "igpu" (the larger 3B model) is preferred
    #    over "npu" (1.5B) when present. An "igpu" tier with no iGPU op wired
    #    falls back to the NPU draft. Skipped above skip_draft_above (npu:0).
    prior: str | None = None
    failures: tuple = ()
    # PD-1 v2 warn-prompt: text-only degeneration reasons from the most recent
    # gated draft, threaded into the next repair_prompt call. Empty when the
    # draft was clean or no observation has happened yet.
    prior_degen: tuple[str, ...] = ()
    draft_tier = next((t for t in topo.ladder if t in ("npu", "igpu")), None)
    skip = (topo.skip_draft_above is not None
            and route.difficulty >= topo.skip_draft_above)
    if draft_tier and not skip:
        if draft_tier == "igpu" and ops.igpu_draft is not None:
            draft_op, draft_name = ops.igpu_draft, "igpu"
        else:
            if draft_tier == "igpu":
                trace.append("igpu drafter unavailable -> NPU draft")
            draft_op, draft_name = ops.draft, "npu"
        cand = draft_op(query)
        trace.append(f"{draft_name} draft -> {len(cand.text)} chars")
        d = observe(draft_name, cand.text)
        g = ops.gate(cand.text)
        if g.passed:
            trace.append(f"{draft_name} gate PASS")
            return won(cand.text, draft_name, 0)
        trace.append(f"{draft_name} gate FAIL: {g.reason}")
        prior, failures = cand.text, g.failures
        prior_degen = d.text_reasons
        # PD-1 v2 skip-repair: if the draft observation tripped degen at the
        # configured score floor, the prior is "poisoned" -- repairing on it
        # tends to inherit or fixate on the failure mode. DISCARD the prior so
        # the GPU phase does a fresh `generate` (line below) instead of feeding
        # the bad draft into the bounded repair loop. Distinct from the
        # hard-escalate lever (which would skip GPU entirely and hand off to
        # Tier-3). Default-off; the A/B sweep
        # (scripts/skip_repair_validation.py) decides whether to keep it.
        if (CONFIG.skip_repair_on_degen
                and d.score >= CONFIG.skip_repair_score_floor):
            trace.append(
                f"skip-repair: {draft_name} degen score={d.score:.2f} >= "
                f"{CONFIG.skip_repair_score_floor:.2f} -> discard prior, fresh GPU"
            )
            prior, failures, prior_degen = None, (), ()
    elif draft_tier:
        trace.append(f"{draft_tier} draft skipped (difficulty>={topo.skip_draft_above})")

    # 2) GPU phase -- only if this topology includes a gpu tier.
    if "gpu" not in topo.ladder:
        return capped(0)

    # If there is no prior to repair (npu skipped/absent), the first GPU call is
    # a fresh generate; otherwise every GPU call repairs the best prior.
    if prior is None:
        cand = ops.generate(query)
        trace.append(f"gpu generate -> {len(cand.text)} chars")
        if not cand.available:
            trace.append("gpu unavailable")
            return capped(0)
        d = observe("gpu", cand.text)
        g = ops.gate(cand.text)
        if g.passed:
            trace.append("gpu gate PASS")
            return won(cand.text, "gpu", 0)
        prior, failures = cand.text, g.failures
        prior_degen = d.text_reasons

    # 3) Bounded repair loop -- the DETERMINISTIC CAP. range stops at cap, so a
    #    (cap+1)'th round cannot happen, pass-or-fail.
    for rnd in range(1, topo.repair_cap + 1):
        # PD-1 v2 warn-prompt: REVERTed to default-off (FINDINGS-pd1-v2-warn-
        # prompt.md). Only thread degen reasons when explicitly enabled.
        degen_for_repair = prior_degen if CONFIG.warn_prompt_enabled else ()
        if degen_for_repair:
            trace.append(
                f"warn-prompt[round {rnd}]: threading "
                f"{len(degen_for_repair)} degen reason(s) into repair"
            )
        rq = ops.repair_prompt(query, prior, failures, degen_for_repair)
        cand = ops.generate(rq)
        trace.append(f"gpu repair round {rnd} -> {len(cand.text)} chars")
        if not cand.available:
            trace.append("gpu unavailable")
            return capped(rnd - 1)
        # Tier token stays a clean key ("gpu") so downstream parsers (e.g.
        # the planned SD-2 dashboard panel) can split degen[<tier>]: by the
        # bracket contents. The repair-round number is already in the
        # "gpu repair round {rnd}" trace line emitted just above.
        d = observe("gpu", cand.text)
        g = ops.gate(cand.text)
        if g.passed:
            trace.append(f"gpu gate PASS (repair round {rnd})")
            return won(cand.text, "gpu", rnd)
        prior, failures = cand.text, g.failures
        prior_degen = d.text_reasons

    return capped(topo.repair_cap)
