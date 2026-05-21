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


# The table. `balanced` reproduces today's cascade (NPU draft -> GPU repair loop
# -> Tier-3 on cap). `low_power` is NPU-only (repair_cap 0: any gate fail goes
# straight to Tier-3, never spinning the GPU). S2 adds a `hard_task` row
# (skip_draft_above) once the skip is validated end-to-end.
TOPOLOGIES: dict[str, Topology] = {
    "balanced": Topology("balanced", ("npu", "gpu")),
    "low_power": Topology("low_power", ("npu",), repair_cap=0),
}

DEFAULT_TOPOLOGY = "balanced"


def get(name: str) -> Topology:
    """Look up a topology by name; raise a clear, listing error on a typo so a
    bad `--topology` fails loud rather than silently defaulting."""
    try:
        return TOPOLOGIES[name]
    except KeyError:
        valid = ", ".join(sorted(TOPOLOGIES))
        raise KeyError(f"unknown topology {name!r}; valid: {valid}") from None
