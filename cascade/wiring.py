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

from cascade import mesh
from cascade.feedback import CheckFailure, build_repair_prompt
from cascade.verifier import verify


def gate(text: str) -> mesh.GateInfo:
    """Syntax gate as a mesh op: pass/fail + a repair-legible failure."""
    v = verify(text)
    if v.passed:
        return mesh.GateInfo(True)
    fail = CheckFailure(
        expr="a syntactically valid Python code block",
        observed=v.reason,
        requirement="the answer must contain one fenced Python block that compiles",
    )
    return mesh.GateInfo(False, (fail,), v.reason)


def repair_prompt(query: str, prior: str, failures: tuple) -> str:
    """Build the model-legible repair request (cascade.feedback) from the
    gate's failures, defaulting to a generic note if none were supplied."""
    fails = list(failures) or [
        CheckFailure("verification", "the previous answer failed the gate", "")
    ]
    return build_repair_prompt(query, prior, fails)


def build_ops(npu, gpu, igpu=None) -> mesh.Ops:
    """Bind live NPU/GPU worker handles into `mesh.Ops`.

    `npu` exposes `.route`/`.draft`; `gpu` exposes `.available()`/`.generate()`.
    `igpu` (optional) exposes `.draft` -- the larger Tier-1b model on the iGPU;
    when None, `igpu_draft` stays None and topologies naming "igpu" fall back to
    the NPU draft (mesh.solve handles that). The closures translate worker
    dataclasses to the mesh boundary types and nothing else."""

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

    # PD-1 v1: snapshot tier availability once and reuse. NPU is binary (the
    # worker only exists if make_npu_worker() compiled), GPU has a live probe.
    # Statuses don't change mid-session in practice -- the memo holds.
    cache: dict[str, bool] = {}

    def tier_status() -> dict[str, bool]:
        if not cache:
            cache["npu"] = True
            cache["gpu"] = bool(gpu.available())
            if igpu is not None:
                cache["igpu"] = True
        return dict(cache)

    return mesh.Ops(
        route=route, draft=draft, generate=generate,
        gate=gate, repair_prompt=repair_prompt, igpu_draft=igpu_draft,
        tier_status=tier_status,
    )
