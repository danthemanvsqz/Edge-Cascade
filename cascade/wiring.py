"""Real-tier Ops adapter -- bind the live workers + verifier into `mesh.Ops`.

The single place that maps the worker value-objects (npu_worker / gpu_worker)
and the deterministic gate (verifier / feedback) onto the transport-agnostic op
boundary that `mesh.solve` calls. Keeping the mapping HERE -- not in mesh.py --
is what lets `mesh` stay pure and 100% testable, and it is the seam the Celery
substrate later re-implements: the ops become task signatures, this adapter is
what changes, and `mesh.solve` does not. See docs/CELERY-READINESS.md (inv. 1).

The in-process gate is syntax-only (cascade.verifier); the functional gate
(checks.dsl sandbox) lives on the edge-verify MCP path. Because the gate is just
an injected op, upgrading the in-process path to functional verification later
is a one-line swap here, not a change to the orchestrator.
"""
from __future__ import annotations

from cascade import mesh, tasks
from cascade.feedback import CheckFailure


def gate(text: str) -> mesh.GateInfo:
    """Syntax gate as a mesh op: pass/fail + a repair-legible failure.
    Delegates to tasks.verify_syntax so both the in-process and Canvas paths
    produce the same edge-verify.rec records."""
    result = tasks.verify_syntax(text)
    if result.get("passed"):
        return mesh.GateInfo(True)
    fail = CheckFailure(
        expr="a syntactically valid Python code block",
        observed=result.get("reason", ""),
        requirement="the answer must contain one fenced Python block that compiles",
    )
    return mesh.GateInfo(False, (fail,), result.get("reason", ""))


def repair_prompt(
    query: str, prior: str, failures: tuple, degen_reasons: tuple = (),
) -> str:
    """Build the model-legible repair request. Delegates to tasks.repair_prompt
    so both the in-process and Canvas paths produce edge-verify.rec records."""
    fails_list = [
        {"expr": f.expr, "observed": f.observed, "requirement": f.requirement}
        for f in (failures or [
            CheckFailure("verification", "the previous answer failed the gate", "")
        ])
    ]
    return tasks.repair_prompt(
        query, prior, fails_list,
        degen_reasons=list(degen_reasons) if degen_reasons else None,
    )


def build_ops(npu, gpu, igpu=None, observe_emit=None) -> mesh.Ops:
    """Bind live NPU/GPU worker handles into `mesh.Ops`.

    `npu` exposes `.route`/`.draft`; `gpu` exposes `.available()`/`.generate()`.
    `igpu` (optional) exposes `.draft` -- the larger Tier-1b model on the iGPU;
    when None, `igpu_draft` stays None and topologies naming "igpu" fall back to
    the NPU draft (mesh.solve handles that). The closures translate worker
    dataclasses to the mesh boundary types and nothing else.

    CONTRACT (PD-1 tier_status): callers must pass `None` (not a stub handle)
    for any unavailable tier. `tier_status` reports `npu`/`igpu` as available
    when the corresponding argument is not None -- presence == compiled, by
    the invariant that `make_npu_worker()` only returns a worker on successful
    compile. A stub worker would silently lie about tier health."""

    def route(q: str) -> mesh.RouteInfo:
        r = npu.route(q)
        return mesh.RouteInfo(r.difficulty, r.category)

    def draft(q: str) -> mesh.Candidate:
        return mesh.Candidate(npu.draft(q).text)

    def generate(q: str) -> mesh.Candidate:
        if not gpu.available():
            return mesh.Candidate("", available=False)
        g = gpu.generate(q)
        return mesh.Candidate(g.text, available=g.available)

    igpu_draft = None
    if igpu is not None:
        def igpu_draft(q: str) -> mesh.Candidate:
            return mesh.Candidate(igpu.draft(q).text)

    # PD-1 v1: snapshot tier availability ONCE at build_ops time. NPU/iGPU
    # presence is binary (per the build_ops contract above -- a None handle
    # means "tier unavailable", a real worker means "compiled"); GPU has a
    # live probe. Eager populate so concurrent tier_status() calls under a
    # future Celery substrate can't race the cache.
    snapshot: dict[str, bool] = {"npu": True, "gpu": bool(gpu.available())}
    if igpu is not None:
        snapshot["igpu"] = True

    def tier_status() -> dict[str, bool]:
        return dict(snapshot)

    return mesh.Ops(
        route=route, draft=draft, generate=generate,
        gate=gate, repair_prompt=repair_prompt, igpu_draft=igpu_draft,
        tier_status=tier_status, observe_emit=observe_emit,
    )
