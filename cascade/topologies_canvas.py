"""Named topologies as Celery Canvas signatures -- the tunable knob, lifted.

This is the structural counterpart of `cascade.topologies` (which holds the
named in-process strategies as data). Each Canvas signature here maps 1:1 onto
the corresponding `Topology` entry, but the executor underneath is Celery
instead of the in-process `mesh.solve` orchestrator (charter inv. 3:
composition over named topologies; charter inv. 5: `.rec` stays at the op
boundary, the executor below is what changed).

Phase 1 ships ONE signature: `budget`. Phase 2 adds `low_latency` (a
`chord` racing NPU draft vs GPU generate) and `low_power` (no GPU at all).

CHAIN SHAPE (budget):

    chain(
      _budget_route.s(env)       # NPU route -> env["difficulty"]
      _budget_draft.s()          # NPU draft if not skip_draft_above
      _budget_verify.s()         # gate the draft -> env["_gate_passed"] + trace
      _budget_resolve_npu.s()    # PASS -> resolve @ npu; FAIL -> carry failures forward
      _budget_gpu_solve.s()      # self.replace() into spike's bounded repair loop
      _budget_cloud.s()          # paid escalation on cap (no-op if cloud disabled)
    )

Each step takes and returns the SAME envelope dict (D1 in
docs/PLAN-canvas-phase1.md). A step that finds `env["resolved"] or
env["capped"]` set returns its input unchanged -- the resolved-shortcut
pattern means the chain still walks, but only the unresolved steps do work.

THE CAP (the load-bearing invariant): lives inside `gpu_solve_task` as
`max_retries=CONFIG.repair_cap`, proven by the Phase-0 spike on both eager
and live-broker paths. Slice 3's chain composition cannot breach the cap
because the cap is a property of the TASK, not the graph (see
docs/FINDINGS-canvas-repair-retry-spike.md). The chain merely hands off to
the proven retrying task via `self.replace()`.

GPU-SOLVE HANDOFF (the one Slice 3-specific risk this de-risks): the chain
step `_budget_gpu_solve` calls `self.replace(chain(gpu_solve_task,
_merge_gpu))` so the spike's retrying task runs in this slot, then its
terminal dict ({answer, final_tier, rounds, ...}) is folded back into the
envelope before the next outer step sees it. Probe-verified in eager mode
(see Slice 3 PR description). Live-broker verification rides on Slice 4's
findings doc.
"""
from __future__ import annotations

import logging

from celery import chain, chord, group

from cascade import canvas_spike, tasks
from cascade import topologies as topo_module
from cascade.celery_app import app
from cascade.config import CONFIG
from cascade.gate import gate as _gate_impl
from cascade.low_latency_pick import _pick_decision
from cascade.tasks import record_npu_win as _record_npu_win

_log = logging.getLogger(__name__)

# The local mesh "wins" when one of these tiers resolved the run on its own.
# capped->tier3 (the bounded repair loop exhausted) and cloud (paid escalation)
# are losses for the local pipe.
_LOCAL_TIERS = frozenset({"npu", "igpu", "gpu"})


def _new_envelope(query: str, dsl: str | None, topology: str) -> dict:
    """Initial envelope -- the SOLE state the chain threads. JSON-clean
    (charter inv. 2): plain types, no live handles. Field semantics match
    `mesh.Outcome` so the client-side adapter (`cascade.canvas_client`) can
    swap callers without a shape translation."""
    return {
        # Final-shape fields, populated as the chain progresses.
        "answer": None,
        "final_tier": "",
        "resolved": False,
        "capped": False,
        "repair_rounds": 0,
        "difficulty": 0.0,
        "topology": topology,
        "trace": [],
        # Carry-forward fields the next step needs.
        "query": query,
        "dsl": dsl,
        "prior": None,
        "failures": [],
    }


def _shortcut(env: dict) -> bool:
    """A step is no-op when the chain already has its answer (resolved) or
    has handed off to Tier-3/cloud (capped). The chain still walks every
    step; this just makes each step a pass-through when its work is moot."""
    return bool(env.get("resolved") or env.get("capped"))


@app.task(name="mesh.budget._route", queue="npu", bind=True)
def _budget_route(self, env: dict) -> dict:
    """Step 1: NPU route. Fills env["difficulty"] + appends a trace line.
    Calling the recorded `tasks.route` directly (not via the Celery wrapper)
    keeps the .rec write at this op boundary (charter inv. 5) without a
    second dispatch round-trip."""
    if _shortcut(env):
        return env
    r = tasks.route(env["query"])
    env["difficulty"] = r.get("difficulty", 0.0)
    env["trace"].append(
        f"route difficulty={env['difficulty']:.2f} "
        f"category={r.get('category', 'standard')}"
    )
    return env


@app.task(name="mesh.budget._draft", queue="npu", bind=True)
def _budget_draft(self, env: dict) -> dict:
    """Step 2: NPU draft (skipped above the route-difficulty threshold per
    the budget topology's `skip_draft_above`). Fills env["prior"] with the
    candidate text for the gate step to consume."""
    if _shortcut(env):
        return env
    budget = topo_module.get("budget")
    if topo_module.should_skip_draft(
            env["difficulty"], env["query"],
            budget.skip_draft_above, CONFIG.skip_draft_min_chars):
        env["trace"].append(
            f"npu draft skipped (difficulty>={budget.skip_draft_above}, "
            f"len>={CONFIG.skip_draft_min_chars})"
        )
        return env
    cand = tasks.draft(env["query"])
    if not cand.get("available", True):
        env["trace"].append("npu unavailable -> GPU phase from scratch")
        return env
    env["prior"] = cand.get("text", "")
    env["trace"].append(f"npu draft -> {len(env['prior'])} chars")
    return env


def _gate(text: str, dsl: str | None) -> tuple[bool, list]:
    """Apply the appropriate gate based on language + DSL.

    Delegates to cascade.gate (VR-4): language-keyed registry with full
    support for Python, TypeScript, git, bash, and JavaScript drafts.

    Returns (passed, failures-list). Failures-list is JSON-clean so it
    rides the chain envelope cleanly across the broker (charter inv. 2)."""
    return _gate_impl(text, dsl)


def _verify_step(env: dict, gate_fn=_gate) -> dict:
    """Pure step: gate the NPU draft. Sets env["_gate_passed"] (bool) and
    appends a trace line. Injectable gate_fn means tests call this directly
    with a fake gate instead of mocking at the module boundary."""
    if _shortcut(env) or not env["prior"]:
        return env
    passed, failures = gate_fn(env["prior"], env["dsl"])
    env["_gate_passed"] = passed
    if passed:
        env["trace"].append("npu gate PASS")
    else:
        env["failures"] = failures
        env["trace"].append("npu gate FAIL")
    return env


@app.task(name="mesh.budget._verify", queue="verify", bind=True)
def _budget_verify(self, env: dict) -> dict:
    """Step 3: gate the NPU draft via the injectable _verify_step helper."""
    return _verify_step(env)


def _resolve_step(env: dict) -> dict:
    """Pure step: resolve the run at NPU on a gate PASS. No-op when the
    verify step was skipped (_gate_passed absent) or the gate failed."""
    if "_gate_passed" not in env or not env["_gate_passed"]:
        return env
    env["answer"] = env["prior"]
    env["final_tier"] = "npu"
    env["resolved"] = True
    return env


@app.task(name="mesh.budget._resolve_npu", queue="verify", bind=True)
def _budget_resolve_npu(self, env: dict) -> dict:
    """Step 4: finalise an NPU win (gate PASS) or carry failures to GPU."""
    if _shortcut(env):
        return env
    env = _resolve_step(env)
    if env.get("resolved"):
        # Write an edge-verify.rec particle so NPU wins appear in the
        # dashboard event log (tool=resolve_npu, tier=npu).
        _record_npu_win(tier=env.get("final_tier", "npu"))
    return env


@app.task(name="mesh.budget._gpu_solve", queue="gpu", bind=True)
def _budget_gpu_solve(self, env: dict):
    """Step 4: the headline -- hand off to the spike's bounded GPU repair
    loop. Uses `self.replace(chain(gpu_solve_task, _merge_gpu_into_env))`:

    - `gpu_solve_task` runs with its `max_retries=CONFIG.repair_cap` cap (the
      structural cap the spike proved holds eager + broker).
    - `_merge_gpu_into_env` folds gpu_solve_task's terminal dict back into
      the envelope so the next outer step (`_budget_cloud`) sees the
      envelope shape, not the gpu_solve_task shape.
    - The OUTER chain continues to `_budget_cloud` after the merge,
      because `self.replace` inserts the replacement IN PLACE.

    The cap cannot be breached by this composition because the cap is a
    property of `gpu_solve_task.max_retries`, not the graph."""
    if _shortcut(env):
        return env
    env["trace"].append("gpu solve (bounded repair loop)")
    return self.replace(
        chain(
            # Slice 3b: ensure qwen14b is resident before generate. The
            # arbiter's swap is idempotent + the factory returns the
            # already-loaded module-level `_gpu`, so this is effectively
            # free under the current single-model registration. Slice 3c
            # adds a second model where the swap actually swaps.
            # `.si()` on gpu_solve_task makes it IMMUTABLE: the swap's
            # return dict is not prepended as the next task's first
            # positional arg (which would collide with the explicit
            # `query=` kwarg below and raise TypeError). swap_task's
            # `.s()` is fine -- it has no upstream to mutate.
            tasks.swap_task.s("qwen14b"),
            canvas_spike.gpu_solve_task.si(
                query=env["query"], dsl=env["dsl"], prior=env["prior"],
                # Canvas->pipe round alignment (Slice 6a): a non-empty prior is
                # the failed NPU/iGPU draft, so gpu_solve_task's FIRST generate
                # already repairs it = round 1 (round_base=1), bounding the run
                # to `cap` GPU calls like mesh.solve's range(1, cap+1). No prior
                # (skip-draft / NPU unavailable) => fresh generate = round 0.
                round_base=1 if env["prior"] else 0,
            ),
            _merge_gpu_into_env.s(env=env),
        )
    )


@app.task(name="mesh.budget._merge_gpu", queue="gpu", bind=True)
def _merge_gpu_into_env(self, gpu_result: dict, env: dict) -> dict:
    """Fold gpu_solve_task's terminal dict ({answer, final_tier, rounds, ...})
    into the envelope. `env` is captured by signature at chain-build time;
    `gpu_result` is the previous chain step's return.

    Two paths:
    - gpu_solve_task succeeded => resolved=True, final_tier="gpu", record rounds.
    - gpu_solve_task capped    => capped=True, record rounds for the cloud step."""
    rounds = int(gpu_result.get("rounds", 0))
    env["repair_rounds"] = rounds
    if gpu_result.get("final_tier") == "gpu":
        env["resolved"] = True
        env["answer"] = gpu_result.get("answer")
        env["final_tier"] = "gpu"
        env["trace"].append(
            "gpu gate PASS"
            + (f" (repair round {rounds})" if rounds else "")
        )
    else:
        env["capped"] = True
        env["trace"].append(
            f"-> {gpu_result.get('final_tier', 'capped->tier3')}"
        )
    return env


@app.task(name="mesh.budget._cloud", queue="gpu", bind=True)
def _budget_cloud(self, env: dict) -> dict:
    """Step 5: paid escalation if capped AND CONFIG.enable_cloud. When cloud
    is disabled, `capped=True` rides forward to the client and gets surfaced
    as the `capped->tier3` Outcome -- same hand-off as today's cascade when
    locals exhaust without --cloud.

    Runs on the `gpu` queue, not `cloud`: this orchestration step calls
    `tasks.cloud_generate` INLINE (not via .apply_async()), so it must run
    on a queue some worker actually consumes -- otherwise the chain's .get()
    blocks forever waiting for the step to execute. The cloud-spend
    invariant is preserved at CONFIG level (`_cloud.enabled` returns the
    disabled hand-off when CONFIG.enable_cloud is False or no API key) and
    at the TASK level (`cloud_generate_task.queue="cloud"`, which still has
    no worker by default -- the structural unspendability of that task is
    unchanged). The earlier Slice-4 live run surfaced this: a chain step
    on an unconsumed queue is a hang, not a hand-off."""
    if env.get("resolved") or not env.get("capped"):
        return env
    if not CONFIG.enable_cloud:
        env["trace"].append("cloud disabled -> caller takes over")
        return env  # capped stays True; client surfaces capped->tier3.
    c = tasks.cloud_generate(env["query"])
    if c.get("available"):
        env["resolved"] = True
        env["capped"] = False
        env["answer"] = c.get("text")
        env["final_tier"] = "cloud"
        env["trace"].append("cloud generate -> resolved")
    else:
        env["trace"].append(
            f"cloud unavailable: {c.get('reason', 'unknown')}"
        )
    return env


@app.task(name="mesh.budget._done", queue="verify", bind=True)
def _budget_done(self, env: dict) -> dict:
    """Step 6 (end of pipe): classify the finished run as a local WIN or a
    LOSS and emit a win/lose log line. A win is the local mesh (NPU/iGPU/GPU)
    resolving on its own; a capped->tier3 hand-off or a paid cloud escalation
    is a loss. Pure marker -- returns env unchanged (never alters the answer
    or resolution), so appending it to the chain can't change an outcome.

    Unlike the other steps it does NOT `_shortcut`: by the end of the pipe
    `resolved`/`capped` are exactly what it reads to make the call."""
    final_tier = env.get("final_tier") or "capped->tier3"
    won = bool(env.get("resolved")) and final_tier in _LOCAL_TIERS
    if won:
        _log.info("cascade WIN  -- local pipe resolved @ %s", final_tier)
        env["trace"].append(f"done: WIN (local @ {final_tier})")
    else:
        _log.info("cascade LOSE -- local pipe yielded -> %s", final_tier)
        env["trace"].append(f"done: LOSE (-> {final_tier})")
    return env


def budget_signature(query: str, dsl: str | None = None):
    """Build the Canvas signature for the `budget` topology. The client
    dispatches this with `.apply_async()` and blocks on `.get()` for the
    final envelope (see `cascade.canvas_client.solve_budget_canvas`)."""
    env = _new_envelope(query, dsl, topology="budget")
    return chain(
        _budget_route.s(env),
        _budget_draft.s(),
        _budget_verify.s(),
        _budget_resolve_npu.s(),
        _budget_gpu_solve.s(),
        _budget_cloud.s(),
        _budget_done.s(),
    )


# ---------------------------------------------------------------------------
# low_latency -- Slice 6b. The headline composition Phase 2 was building
# toward (subsumes the old P2c "speculative GPU"). Instead of the budget
# chain's SEQUENTIAL route -> draft -> gate -> swap -> generate, race the NPU
# draft and the GPU generate CONCURRENTLY (a chord's group) and take the first
# candidate that verifies. The latency win lands when the NPU draft usually
# FAILS the gate (the 2026-05-20 finding for hard tasks): budget pays npu +
# gpu back-to-back, low_latency overlaps them.


@app.task(name="mesh.low_latency._pick", queue="verify", bind=True)
def _pick_first_verified(self, results: list[dict], env: dict) -> dict:
    """Chord callback: gate the raced candidates, resolve to the first VERIFIED.

    `results` is the group's output in arm order: [npu_draft_result,
    gpu_generate_result]. Preference order is cheapest-first (npu before gpu):
    a verified NPU draft wins because Tier-1 is ~free; otherwise the GPU
    candidate; otherwise capped->Tier-3.

    Celery semantics that shape this (documented in the findings): a chord
    callback fires only AFTER ALL group members finish, so this is "first in
    PREFERENCE order that verifies", NOT "first to FINISH" -- there is no
    early-kill of the slower arm. The win is concurrency of the two arms, not
    cancellation. And low_latency is the FAST speculative path: it deliberately
    does NOT enter the bounded GPU repair loop (that is `budget`'s job) -- a
    double-miss hands straight to Tier-3. Trades GPU cost (the generate always
    runs) for latency; a per-workload topology choice, never a default."""
    # `env` was captured by signature at dispatch time; the group arms return
    # their OWN dicts (`results`) and never mutate the envelope, so reading the
    # frozen `env` here is correct. (If an arm were ever changed to mutate
    # envelope state, that mutation would be silently dropped -- keep arms pure.)
    return _pick_decision(results, env, gate_fn=_gate)


def low_latency_signature(query: str, dsl: str | None = None):
    """Build the `low_latency` Canvas signature -- a chord racing the NPU draft
    against the GPU generate, callback picks the first verified candidate.

    The GPU arm prepends `model.swap("qwen14b")` so the production coder is
    resident before generate (same guarantee as `budget`'s gpu_solve handoff);
    `.si()` keeps generate immutable so the swap's return dict isn't injected as
    its prompt. No route step -- low_latency speculates rather than routing
    (charter: topology IS the routing decision). Dispatched via
    `cascade.canvas_client.solve_low_latency_canvas`."""
    env = _new_envelope(query, dsl, topology="low_latency")
    return chord(
        group(
            tasks.draft_task.s(query),
            chain(
                tasks.swap_task.s("qwen14b"),
                tasks.generate_qwen14b_task.si(query),
            ),
        ),
        _pick_first_verified.s(env=env),
    )
