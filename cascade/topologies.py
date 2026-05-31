"""Named mesh topologies -- the tunable knob, expressed as DATA.

A topology declares how `cascade.mesh.solve` composes the tier ops: which tiers
to attempt, in what order, with what deterministic repair-round cap, and an
optional difficulty above which the Tier-1 NPU draft is skipped. Selection is by
name (a config value, not a code branch), so adding a routing strategy is one
row here -- never an edit to the orchestrator.

This is the in-process seam the Celery substrate later snaps onto: each
`Topology` maps 1:1 onto a Canvas signature (`chain`/`group`/`chord`). See
docs/CELERY-READINESS.md (invariant 3) and docs/DESIGN-celery-canvas.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cascade.config import CONFIG


@dataclass(frozen=True)
class Topology:
    """One mesh strategy.

    `ladder`            ordered tiers to attempt (subset of "npu", "gpu").
    `repair_cap`        max GPU repair rounds before Tier-3 takeover. Defaults
                        to the single-source CONFIG.repair_cap; the loop in
                        mesh.solve is `range(1, cap+1)` so it cannot be exceeded.
    `skip_draft_above`  if set, skip the Tier-1 NPU draft when routed difficulty
                        is >= this (the always-failing-NPU finding -> S2).
    """

    name: str
    ladder: tuple[str, ...]
    repair_cap: int = field(default_factory=lambda: CONFIG.repair_cap)
    skip_draft_above: float | None = None


# The table.
TOPOLOGIES: dict[str, Topology] = {
    # budget reproduces the original cascade: NPU draft for routine work, but
    # SKIP the draft once the router flags a task hard (>= the GPU-escalation
    # threshold). The 2026-05-20 review found the 1.5B draft never won on hard
    # tasks (final_tier npu:0), so drafting there was pure latency -- go to GPU.
    "budget": Topology(
        "budget", ("npu", "gpu"),
        skip_draft_above=CONFIG.escalate_to_gpu_difficulty,
    ),
    # low_power: NPU-only, never spins the GPU (repair_cap 0) -- any gate fail
    # caps out to Tier-3 immediately. The frugal, lowest-watt strategy.
    "low_power": Topology("low_power", ("npu",), repair_cap=0),
    # hard_task: skip Tier-1 entirely, GPU-first + repair loop. For a batch you
    # already know is hard, or to force the GPU without paying for a draft.
    "hard_task": Topology("hard_task", ("gpu",)),
    # igpu_assist: draft on the larger iGPU 3B model (Tier-1b) instead of the
    # 1.5B NPU, then GPU repair. The 1.5B fails the dijkstra-class gate 0/9; a
    # 3B draft is the lever. Requires CASCADE_IGPU_MODEL_DIR; falls back to the
    # NPU draft if no iGPU model is wired. See PLAN-observability-tuning.md (C3).
    "igpu_assist": Topology("igpu_assist", ("igpu", "gpu")),
}

DEFAULT_TOPOLOGY = "budget"


def get(name: str) -> Topology:
    """Look up a topology by name; raise a clear, listing error on a typo so a
    bad `--topology` fails loud rather than silently defaulting."""
    try:
        return TOPOLOGIES[name]
    except KeyError:
        valid = ", ".join(sorted(TOPOLOGIES))
        raise KeyError(f"unknown topology {name!r}; valid: {valid}") from None


def should_skip_draft(
    difficulty: float, query: str, threshold: float | None, min_chars: int
) -> bool:
    """Length-aware skip-draft decision (BACKLOG #8).

    Skip the Tier-1 NPU draft only when the task is BOTH flagged hard
    (`difficulty >= threshold`) AND the prompt is long enough
    (`len(query) >= min_chars`) that the NPU attempt is genuinely wasteful.
    Short prompts always get the cheap (~3s) NPU shot: the router over-rates
    short input, so a high score on a one-liner is more likely a mis-rate than a
    truly-hard task, and drafting it is fast. `threshold is None` => never skip.
    """
    return threshold is not None and difficulty >= threshold and len(query) >= min_chars
