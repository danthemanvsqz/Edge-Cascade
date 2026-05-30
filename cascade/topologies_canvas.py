"""Named topologies as Celery Canvas signatures -- the tunable knob, lifted.

This is the structural counterpart of `cascade.topologies` (which holds the
named in-process strategies as data). Each Canvas signature here maps 1:1 onto
the corresponding `Topology` entry, but the executor underneath is Celery
instead of the in-process `mesh.solve` orchestrator (charter inv. 3:
composition over named topologies; charter inv. 5: `.rec` stays at the op
boundary, the executor below is what changed).

Phase 1 ships ONE signature: `balanced`. Phase 2 adds `low_latency` (a
`chord` racing NPU draft vs GPU generate) and `low_power` (no GPU at all).

CHAIN SHAPE (balanced):

    chain(
      _balanced_route.s(env)       # NPU route -> env["difficulty"]
      _balanced_draft.s()          # NPU draft if not skip_draft_above
      _balanced_draft_gate.s()     # verify_functional on draft -> resolve or carry-forward
      _balanced_gpu_solve.s()      # self.replace() into spike's bounded repair loop
      _balanced_cloud.s()          # paid escalation on cap (no-op if cloud disabled)
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
step `_balanced_gpu_solve` calls `self.replace(chain(gpu_solve_task,
_merge_gpu))` so the spike's retrying task runs in this slot, then its
terminal dict ({answer, final_tier, rounds, ...}) is folded back into the
envelope before the next outer step sees it. Probe-verified in eager mode
(see Slice 3 PR description). Live-broker verification rides on Slice 4's
findings doc.
"""
from __future__ import annotations

from celery import chain, chord, group

from cascade import canvas_spike, tasks
from cascade import topologies as topo_module
from cascade import verifier as syntax_verifier
from cascade.celery_app import app
from cascade.config import CONFIG


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


@app.task(name="mesh.balanced._route", queue="npu", bind=True)
def _balanced_route(self, env: dict) -> dict:
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


@app.task(name="mesh.balanced._draft", queue="npu", bind=True)
def _balanced_draft(self, env: dict) -> dict:
    """Step 2: NPU draft (skipped above the route-difficulty threshold per
    the balanced topology's `skip_draft_above`). Fills env["prior"] with the
    candidate text for the gate step to consume."""
    if _shortcut(env):
        return env
    balanced = topo_module.get("balanced")
    if (balanced.skip_draft_above is not None
            and env["difficulty"] >= balanced.skip_draft_above):
        env["trace"].append(
            f"npu draft skipped (difficulty>={balanced.skip_draft_above})"
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
    """Apply the appropriate gate based on whether a DSL is supplied.

    `dsl=None` => SYNTAX gate (cascade.verifier.verify) -- the same gate
    the in-process pipe path uses (cascade.wiring.gate). This is the
    parity contract: a Canvas run without a DSL behaves like
    `mesh.solve(query, "balanced", ops)` on the same prompt.

    `dsl` supplied => FUNCTIONAL gate (tasks.verify_functional -> the
    `_funcverify_child` subprocess sandbox) which exec's the candidate
    against the DSL's assertions. Strict, returns `applicable: false`
    (= passed: false) when no DSL would otherwise be matched.

    Returns (passed, failures-list). Failures-list is JSON-clean so it
    rides the chain envelope cleanly across the broker (charter inv. 2)."""
    if dsl:
        verdict = tasks.verify_functional(text, dsl)
        return bool(verdict.get("passed")), list(verdict.get("failures", ()))
    v = syntax_verifier.verify(text)
    if v.passed:
        return True, []
    return False, [{"expr": "syntax", "observed": v.reason,
                    "requirement": "fenced Python block that compiles"}]


@app.task(name="mesh.balanced._draft_gate", queue="verify", bind=True)
def _balanced_draft_gate(self, env: dict) -> dict:
    """Step 3: gate the NPU draft. PASS => resolve (final_tier="npu").
    FAIL => carry env["prior"]/env["failures"] forward so GPU repairs on
    it (the bounded repair loop). Gate semantics: syntax when env["dsl"]
    is None (parity with the pipe path), functional otherwise."""
    if _shortcut(env) or not env["prior"]:
        return env
    passed, failures = _gate(env["prior"], env["dsl"])
    if passed:
        env["answer"] = env["prior"]
        env["final_tier"] = "npu"
        env["resolved"] = True
        env["trace"].append("npu gate PASS")
        return env
    env["failures"] = failures
    env["trace"].append("npu gate FAIL")
    return env


@app.task(name="mesh.balanced._gpu_solve", queue="gpu", bind=True)
def _balanced_gpu_solve(self, env: dict):
    """Step 4: the headline -- hand off to the spike's bounded GPU repair
    loop. Uses `self.replace(chain(gpu_solve_task, _merge_gpu_into_env))`:

    - `gpu_solve_task` runs with its `max_retries=CONFIG.repair_cap` cap (the
      structural cap the spike proved holds eager + broker).
    - `_merge_gpu_into_env` folds gpu_solve_task's terminal dict back into
      the envelope so the next outer step (`_balanced_cloud`) sees the
      envelope shape, not the gpu_solve_task shape.
    - The OUTER chain continues to `_balanced_cloud` after the merge,
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


@app.task(name="mesh.balanced._merge_gpu", queue="gpu", bind=True)
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


@app.task(name="mesh.balanced._cloud", queue="gpu", bind=True)
def _balanced_cloud(self, env: dict) -> dict:
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


def balanced_signature(query: str, dsl: str | None = None):
    """Build the Canvas signature for the `balanced` topology. The client
    dispatches this with `.apply_async()` and blocks on `.get()` for the
    final envelope (see `cascade.canvas_client.solve_balanced_canvas`)."""
    env = _new_envelope(query, dsl, topology="balanced")
    return chain(
        _balanced_route.s(env),
        _balanced_draft.s(),
        _balanced_draft_gate.s(),
        _balanced_gpu_solve.s(),
        _balanced_cloud.s(),
    )


# ---------------------------------------------------------------------------
# low_latency -- Slice 6b. The headline composition Phase 2 was building
# toward (subsumes the old P2c "speculative GPU"). Instead of the balanced
# chain's SEQUENTIAL route -> draft -> gate -> swap -> generate, race the NPU
# draft and the GPU generate CONCURRENTLY (a chord's group) and take the first
# candidate that verifies. The latency win lands when the NPU draft usually
# FAILS the gate (the 2026-05-20 finding for hard tasks): balanced pays npu +
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
    does NOT enter the bounded GPU repair loop (that is `balanced`'s job) -- a
    double-miss hands straight to Tier-3. Trades GPU cost (the generate always
    runs) for latency; a per-workload topology choice, never a default."""
    # `env` was captured by signature at dispatch time; the group arms return
    # their OWN dicts (`results`) and never mutate the envelope, so reading the
    # frozen `env` here is correct. (If an arm were ever changed to mutate
    # envelope state, that mutation would be silently dropped -- keep arms pure.)
    draft_res = results[0] if len(results) > 0 else {}
    gpu_res = results[1] if len(results) > 1 else {}
    for tier, res in (("npu", draft_res), ("gpu", gpu_res)):
        # A non-dict / unavailable / empty-text arm is never gated -- skip it.
        # The isinstance guard must cover the .get below too (a non-dict result
        # would otherwise raise AttributeError, not hand off cleanly).
        text = res.get("text", "") if isinstance(res, dict) else ""
        if not (isinstance(res, dict) and res.get("available", True) and text):
            env["trace"].append(f"low_latency: {tier} race candidate unavailable")
            continue
        passed, _ = _gate(text, env["dsl"])
        env["trace"].append(
            f"low_latency: {tier} race candidate gate "
            f"{'PASS' if passed else 'FAIL'}"
        )
        if passed:
            env["answer"] = text
            env["final_tier"] = tier
            env["resolved"] = True
            return env
    env["capped"] = True
    env["trace"].append(
        "low_latency: neither raced candidate verified -> capped->tier3"
    )
    return env


def low_latency_signature(query: str, dsl: str | None = None):
    """Build the `low_latency` Canvas signature -- a chord racing the NPU draft
    against the GPU generate, callback picks the first verified candidate.

    The GPU arm prepends `model.swap("qwen14b")` so the production coder is
    resident before generate (same guarantee as `balanced`'s gpu_solve handoff);
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
