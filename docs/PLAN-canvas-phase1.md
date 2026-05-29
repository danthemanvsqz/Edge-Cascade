# PLAN — Canvas port Phase 1 (post-spike: tier ops + balanced topology as a chain)

> Status: **proposed plan, pending Slice 1.** Companion to
> [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md) (Phases 0/1/2/3 sequencing),
> [CELERY-READINESS.md](CELERY-READINESS.md) (the six invariants), and
> [FINDINGS-canvas-repair-retry-spike.md](FINDINGS-canvas-repair-retry-spike.md)
> (Phase-0 de-risk spike, PROVEN on eager + live broker).

## TL;DR

Lift `route`, `draft`, `cloud_generate` into Celery tasks and compose the
`balanced` topology as a nested Canvas chain dispatched from the client. The
2-round repair cap stays where the spike proved it works: structurally inside
`gpu_solve_task.max_retries`. Pipes + the in-process `mesh.solve` path stay
default; Canvas is opt-in via `uv sync --extra celery` until a topology beats
the hardcoded cascade on a real metric (the Phase-0 decision gate).

The umbrella PR splits into **4 small reviewable slices**.

## State after spike PR #83 (main @ `130dc62`)

Tasks already on the broker:

| Task | Queue | Wraps | Recorder |
|---|---|---|---|
| `generate_task` | `gpu` | `gpu_worker.generate` | `edge-gpu.rec` |
| `verify_functional_task` | `verify` | `mcp_servers._funcverify_child` subprocess | `edge-verify.rec` |
| `gpu_solve_task` (spike) | `gpu` | bounded GPU repair loop; `max_retries=CONFIG.repair_cap` | reuses `_GPU_REC` + `_VERIFY_REC` per attempt |

[cascade/celery_app.py](../cascade/celery_app.py) configures Redis broker +
backend, `worker_prefetch_multiplier=1`, `task_acks_late=True`,
`worker_max_tasks_per_child=0`, `visibility_timeout=3600`.

The in-process orchestrator [cascade/mesh.py](../cascade/mesh.py) `solve(query,
topology, ops)` and the named topology table
[cascade/topologies.py](../cascade/topologies.py) are unchanged by this plan.

## Locked decisions (the three open Qs from planning)

### D1 — Canvas envelope shape: Outcome-dict, one shape end-to-end

Every chain step takes and returns the same dict (JSON-clean — charter inv. 2):

```python
{
    # final-shape fields (populated as the chain progresses)
    "answer": str | None,
    "final_tier": str,          # "npu" | "gpu" | "capped->tier3" | "cloud" | ""
    "resolved": bool,
    "capped": bool,
    "repair_rounds": int,
    "difficulty": float,
    "topology": str,
    "trace": list[str],

    # carry-forward fields the next step needs
    "query": str,               # original query, never mutated
    "dsl": str | None,          # functional gate dsl, never mutated
    "prior": str | None,        # the most recent failed draft (None if no prior)
    "failures": list,           # gate failures shape (JSON-clean)
}
```

Resolved-shortcut: if `resolved=True OR capped=True` on entry, the step returns
its input unchanged. So `chain(step1.s(env), step2.s(), step3.s())` walks the
whole chain but only the unresolved steps do work. The client `.get()` returns
this dict; a thin client wrapper unpacks it into the existing
`mesh.Outcome`-shaped return for callers.

**Why this not a richer `Outcome` dataclass:** `Outcome` is `frozen=True` and
holds `tuple` fields (`failures`, `trace`) that don't survive a JSON round-trip
cleanly without custom encoders. A plain dict on the wire + an
`Outcome.from_envelope(dict) -> Outcome` adapter at the client boundary is one
fewer thing to debug.

### D2 — Gate placement: split (one tier op per task)

Each tier op stays its own task. Slice 1's NPU `draft` does **not** embed the
gate. The chain wires it explicitly:

```
chain(route_task.s(env) | _draft_step.s() | _gate_step.s() | _gpu_solve_step.s() | _cloud_step.s())
```

Where `_draft_step.s` calls `draft_task` (npu queue), `_gate_step.s` calls
`verify_functional_task` (verify queue), `_gpu_solve_step.s` invokes
`gpu_solve_task` (gpu queue, which embeds its own gate calls per attempt
internally — that's the spike's bounded-loop pattern).

**Why split:** matches the spike's split (`generate_task` and
`verify_functional_task` are separate). Hardware-pinning friendly — the verify
queue can sit on either box. Charter inv. 1 (the tier op is the only unit) —
embedding the gate inside `draft_task` would create a `draft+gate` compound op
that doesn't exist anywhere else.

### D3 — iGPU + `igpu_assist` topology: defer to Phase 2

Phase 1 lifts only the ops used by the `balanced` topology (`route` → `draft`
(NPU) → GPU repair loop → optional cloud). The `igpu_assist` topology and the
`Ops.igpu_draft` field stay as-is in [cascade/mesh.py](../cascade/mesh.py); a
chain dispatch that selects `igpu_assist` will fall through to the NPU draft
exactly like today (the existing fallback path in `mesh.solve`).

**Phase 2 will add:** an `igpu_draft_task` (npu queue, or its own `igpu` queue)
and the `igpu_assist` Canvas signature. Out of scope here.

### D4 (bonus, from Risks) — `visibility_timeout=3600s` covers cloud

The 3600s window already covers the longest task on the broker: GPU `generate`
(~180s) and Opus cloud calls (typically <120s). Slice 2 pins this with a test
that asserts a `cloud_generate_task` enqueued without a cloud worker
`.get(timeout=0.5)` raises `TimeoutError` (not redelivered prematurely).

## Slices

Each slice is one PR.  All four together = Phase 1.

### Slice 1 — Lift `route` + `draft` (npu queue)

**Touches:** [cascade/tasks.py](../cascade/tasks.py),
`tests/test_tasks_npu.py` (new).

**Add to `cascade/tasks.py`:**

```python
from cascade.npu_worker import make_npu_worker

_NPU_REC = make_recorder("edge-npu")
_npu = make_npu_worker()


@recorded(_NPU_REC)
def route(query: str) -> dict:
    """Tier-1 route (mirrors mcp_servers/npu.py:route) -> records edge-npu.rec."""
    r = _npu.route(query)
    return {"available": r.available, "difficulty": r.difficulty,
            "category": r.category, "model": r.model,
            "latency_s": round(r.latency_s, 2)}


@recorded(_NPU_REC)
def draft(query: str) -> dict:
    """Tier-1 draft (mirrors mcp_servers/npu.py:draft) -> records edge-npu.rec.
    Returns the same available/text/model/tokens_per_s/latency_s shape as
    `generate` for envelope consistency."""
    if not _npu.available():
        return {"available": False, "model": CONFIG.npu_model,
                "text": "[npu tier unavailable]",
                "tokens_per_s": 0.0, "latency_s": 0.0}
    r = _npu.draft(query)
    return {"available": r.available, "text": r.text, "model": r.model,
            "tokens_per_s": round(r.tokens_per_s, 2),
            "latency_s": round(r.latency_s, 2)}


@app.task(name="mesh.route", queue="npu")
def route_task(query: str) -> dict:
    return route(query)


@app.task(name="mesh.draft", queue="npu")
def draft_task(query: str) -> dict:
    return draft(query)
```

**Tests (`tests/test_tasks_npu.py`):**
- Eager mode (`task_always_eager=True`): `route_task.apply()` returns a dict
  shaped `{available, difficulty, category, model, latency_s}` and writes a
  record to `edge-npu.rec`.
- Eager mode: `draft_task.apply()` returns `{available, text, model, ...}`.
- `available:false` path when NPU unavailable (mock `_npu` via `mocker`).
- `.rec` parity: byte-for-byte identical against
  `mcp_servers/npu.py:route(query)` on a fixed prompt (run both, compare the
  emitted record's keys + values modulo timestamps).

**Cap impact:** none. Single-shot tasks; spike's `gpu_solve_task` unchanged.

**LOC budget:** ~80 prod + ~120 tests.

---

### Slice 2 — Lift `cloud_generate` (cloud queue, no-worker by default)

**Touches:** [cascade/tasks.py](../cascade/tasks.py),
`tests/test_tasks_cloud.py` (new).

**Add to `cascade/tasks.py`:**

```python
from cascade.cloud_worker import make_cloud_worker, reason_note

_CLOUD_REC = make_recorder("edge-cloud")
# enabled here mirrors the runtime: an `available:false` record is the clean
# hand-off if API key is absent (charter inv. 5: status, not error)
_cloud = make_cloud_worker(enabled=CONFIG.enable_cloud)


@recorded(_CLOUD_REC)
def cloud_generate(query: str) -> dict:
    """Tier-3 cloud generate (mirrors mcp_servers/cloud.py) ->
    records edge-cloud.rec.  available:false when paid cloud is disabled
    or no API key is configured."""
    if not _cloud.enabled:
        return {"available": False, "model": _cloud.model,
                "text": "[cloud tier disabled]",
                "latency_s": 0.0, "reason": "disabled"}
    c = _cloud.generate(query)
    return {"available": c.available, "text": c.text, "model": c.model,
            "latency_s": round(c.latency_s, 2),
            "reason": reason_note(c)}


@app.task(name="mesh.cloud_generate", queue="cloud")
def cloud_generate_task(query: str) -> dict:
    return cloud_generate(query)
```

**Spend invariant:** the documented worker launch is `python -m celery -A
cascade.celery_app worker -Q npu,gpu,verify -l info` (no `cloud`). A
`cloud_generate_task.apply_async(args=[q])` from the client enqueues but never
runs → structurally unspendable, same guarantee as today's
`--strict-mcp-config` exclusion.

**Tests (`tests/test_tasks_cloud.py`):**
- Eager mode + cloud enabled (mock `_cloud`): returns the dict shape.
- Eager mode + cloud disabled: `available:false`, `reason:"disabled"`.
- Spend invariant: with a real Celery app but no worker on `cloud` queue,
  `cloud_generate_task.apply_async(args=["x"]).get(timeout=0.5)` raises
  `TimeoutError`. (Pins D4 — visibility_timeout doesn't redeliver early.)

**LOC budget:** ~50 prod + ~80 tests.

---

### Slice 3 — `balanced` topology as nested Canvas chain at dispatch

**Touches:** `cascade/topologies_canvas.py` (new),
`cascade/canvas_client.py` (new), `tests/test_canvas_balanced.py` (new).
Updates [cascade/canvas_spike.py](../cascade/canvas_spike.py) docstring to
reference the new chain (no functional change).

**`cascade/topologies_canvas.py`** maps `topologies.Topology` → Celery signature.
Each step in the chain takes and returns an envelope (D1). Steps are
`@app.task(bind=True, ignore_result=False)` adapters that:
1. Inspect `env["resolved"]` or `env["capped"]` — pass-through if either set.
2. Otherwise call the underlying tier task body via direct call (in-process to
   the chain step) and update the envelope.

Why direct call (not `.apply_async().get()` inside the step):  the step IS the
worker; calling another task's body in-process from inside a step avoids the
worker-blocking-on-children anti-pattern. Hardware pinning still works at the
chain-step level (the step itself runs on its assigned queue's worker).

```python
# Sketch -- final shapes locked at Slice 3 PR time, not now.
@app.task(name="mesh.balanced._route", queue="npu", bind=True)
def _balanced_route(self, env):
    if env["resolved"] or env["capped"]:
        return env
    r = tasks.route(env["query"])
    env["difficulty"] = r["difficulty"]
    env["trace"].append(f"route difficulty={r['difficulty']:.2f}")
    return env

@app.task(name="mesh.balanced._draft", queue="npu", bind=True)
def _balanced_draft(self, env):
    if env["resolved"] or env["capped"]:
        return env
    topo = topologies.get(env["topology"])
    if (topo.skip_draft_above is not None
            and env["difficulty"] >= topo.skip_draft_above):
        env["trace"].append("npu draft skipped (difficulty)")
        return env
    cand = tasks.draft(env["query"])
    env["trace"].append(f"npu draft -> {len(cand['text'])} chars")
    env["prior"] = cand["text"]
    return env

@app.task(name="mesh.balanced._draft_gate", queue="verify", bind=True)
def _balanced_draft_gate(self, env):
    if env["resolved"] or env["capped"] or env["prior"] is None:
        return env
    verdict = tasks.verify_functional(env["prior"], env["dsl"])
    if verdict["passed"]:
        env.update(resolved=True, answer=env["prior"], final_tier="npu",
                   repair_rounds=0)
        env["trace"].append("npu gate PASS")
        return env
    env["failures"] = list(verdict.get("failures", ()))
    env["trace"].append("npu gate FAIL")
    return env

@app.task(name="mesh.balanced._gpu_solve", queue="gpu", bind=True)
def _balanced_gpu_solve(self, env):
    if env["resolved"] or env["capped"]:
        return env
    # gpu_solve_task is the SPIKE's retrying task. Invoking its body directly
    # would lose the retry semantics, so dispatch it as a subordinate task and
    # block on .get() AT THE CLIENT (this step runs on the gpu worker; .get()
    # here IS the anti-pattern). Use `self.replace(gpu_solve_task.signature(...))`
    # so Celery rewrites the chain to run gpu_solve_task in this slot --
    # control returns to the next chain step on its terminal result, no
    # worker-blocking-on-children.
    return self.replace(
        canvas_spike.gpu_solve_task.signature(
            kwargs={"query": env["query"], "dsl": env["dsl"],
                    "prior": env["prior"]},
            link=_balanced_after_gpu.s(env=env),
        ),
    )

@app.task(name="mesh.balanced._after_gpu", queue="gpu", bind=True)
def _balanced_after_gpu(self, gpu_result, env):
    # gpu_result is gpu_solve_task's return: either resolved or capped->tier3
    if gpu_result["final_tier"] == "gpu":
        env.update(resolved=True, answer=gpu_result["answer"],
                   final_tier="gpu", repair_rounds=gpu_result["rounds"])
    else:
        env["capped"] = True
        env["repair_rounds"] = gpu_result["rounds"]
        env["trace"].append(f"-> {gpu_result['final_tier']}")
    return env

@app.task(name="mesh.balanced._cloud", queue="cloud", bind=True)
def _balanced_cloud(self, env):
    if env["resolved"] or not env["capped"]:
        return env
    if not CONFIG.enable_cloud:
        env["trace"].append("cloud disabled -> caller takes over")
        return env  # capped stays True, client returns the cap signal
    c = tasks.cloud_generate(env["query"])
    if c["available"]:
        env.update(resolved=True, answer=c["text"], final_tier="cloud",
                   capped=False)
    return env


def balanced_signature(query, dsl=None):
    env = {"answer": None, "final_tier": "", "resolved": False, "capped": False,
           "repair_rounds": 0, "difficulty": 0.0, "topology": "balanced",
           "trace": [], "query": query, "dsl": dsl,
           "prior": None, "failures": []}
    return chain(
        _balanced_route.s(env),
        _balanced_draft.s(),
        _balanced_draft_gate.s(),
        _balanced_gpu_solve.s(),
        _balanced_cloud.s(),
    )
```

**`cascade/canvas_client.py`:**

```python
def solve_balanced_canvas(query: str, dsl: str | None = None) -> mesh.Outcome:
    """Client entry mirroring cascade.canvas_spike.solve_balanced but for the
    full balanced topology. Issues the chain, blocks on the final result,
    adapts the envelope back to mesh.Outcome so callers swap without changes."""
    env = balanced_signature(query, dsl).apply_async().get()
    return mesh.Outcome(
        answer=env["answer"], final_tier=env["final_tier"] or "capped->tier3",
        resolved=env["resolved"], capped=env["capped"],
        repair_rounds=env["repair_rounds"], difficulty=env["difficulty"],
        topology=env["topology"], trace=tuple(env["trace"]),
    )
```

**Tests (`tests/test_canvas_balanced.py`):**
- Eager-mode parity vs `mesh.solve` on 4 cases (use scripted spies for
  `tasks.route/draft/generate/verify_functional/cloud_generate`):
  - NPU gate PASS → `final_tier="npu"`, no GPU calls.
  - NPU gate FAIL → GPU first attempt PASS → `final_tier="gpu"`.
  - All GPU repairs FAIL → `capped=True`, cloud enabled → `final_tier="cloud"`.
  - All GPU repairs FAIL, cloud disabled → `capped=True`, `final_tier="capped->tier3"`.
- Cap invariant: scripted always-fail GPU. Assert `tasks.generate` called
  `cap+1` times exactly. (Same assertion the spike makes — cap stays in
  `gpu_solve_task.max_retries`, not the composed graph.)
- `skip_draft_above`: scripted high-difficulty route → assert
  `tasks.draft` NOT called.
- Live-broker smoke: real Redis + worker, one balanced run on a trivial query;
  assert envelope returns with `resolved=True` and `.rec` deltas match
  expectations.

**Risk to retire in this slice:** `self.replace()` semantics on a chain step
that hands off to `gpu_solve_task`. If `replace()` proves messy (e.g.
`link=_after_gpu.s(env=env)` doesn't thread `env` cleanly), fallback is the
client-side `.get()` approach (the `_balanced_gpu_solve` step blocks on
`gpu_solve_task.apply_async().get()` — pays the worker-blocking-on-children
cost but only at one slot; the spike already proved the inner task). Decision
captured in the slice's PR.

**LOC budget:** ~120 prod + ~200 tests (eager-only) + ~40 broker smoke.

---

### Slice 4 — Wire-up + opt-in client CLI + findings

**Touches:** `scripts/mesh_solve_canvas.py` (new),
`docs/FINDINGS-canvas-phase1.md` (new), `cascade/canvas_client.py` docstring
finalization.

**`scripts/mesh_solve_canvas.py`:** CLI wrapping `solve_balanced_canvas`.
Usage:

```
uv run python scripts/mesh_solve_canvas.py "write a python function add(a, b) -> a + b"
```

Prints the answer + the final tier + the trace lines, same shape as
`scripts/mesh_solve.py` does for the Phase-0 spike.

**Findings doc `docs/FINDINGS-canvas-phase1.md`:** parity proof.
- Pick 3 cases from the RUNBOOK (NPU pass, NPU fail→GPU pass, cap→cloud or
  cap→tier3).
- Run each through `cli.py` (pipe path) and `mesh_solve_canvas.py` (Canvas).
- Assert `.rec` deltas (record counts per lane, tier attribution) match modulo
  timestamps.
- Capture wall time per case — Canvas should be within ~50ms of pipe path
  (broker round-trip <10ms × number of hops). NOT a performance argument; just
  showing parity.

**No change** to `orchestrator.py`/`cli.py`/`mesh.py`/MCP servers. Canvas stays
opt-in until a topology proves out (Phase-0 decision gate).

**LOC budget:** ~40 prod + findings doc.

---

## Out of scope (Phase 2+)

- iGPU op + `igpu_assist` topology — D3 above.
- `low_latency` chord (`group(draft, generate)` race) — per DESIGN-celery-canvas
  but not in Phase 1.
- Hardware pinning across boxes (NPU worker → Intel box, GPU worker → RTX
  box) — backlog item "bare-metal Celery workers" depends on Phase 1
  landing single-box first.
- `.rec` aggregation across hosts — only matters when multi-box lands.
- Selector that picks a topology from a config value end-to-end (the agent
  passes `(query, topology)` and Canvas dispatches) — Phase 2 once `balanced`
  proves out.

## Risks + mitigations

| Risk | Slice | Mitigation |
|---|---|---|
| `self.replace()` semantics on the gpu-solve chain step | Slice 3 | Fallback: client-side `.get()` from the step (one slot only); decision captured in PR |
| Envelope mutation across chain steps (Celery serializes between hops; in-place dict mutation gets re-serialized — should be fine on JSON but pin with a test) | Slice 3 | Test: scripted spy that returns a sentinel-bearing envelope; assert sentinel reaches the next step intact |
| `cloud_generate_task` enqueued with no worker → ghost messages in Redis | Slice 2 | Documented worker launch excludes `cloud`; `.apply_async(ignore_result=True)` discards backend entry; tested in Slice 2 |
| `@recorded` recorder per-module-level handle vs Celery worker process model — does `make_recorder` survive worker fork? | Slice 1 | Pin with a test that runs `route_task` twice in one worker, asserts `_seq` increments monotonically (run_id stable, charter inv. 5) |

## Charter invariant check (D-table)

| Invariant | How Phase 1 satisfies it |
|---|---|
| 1. Tier op is the only unit | Each task wraps exactly one `@recorded` worker fn; chain steps are envelope-adapters, not tier reimplementations |
| 2. Op boundary = serializable data | The envelope is JSON-clean; no live handles cross the wire |
| 3. Composition = named topology, expressed as data | `balanced_signature()` returns a Canvas chain that maps 1:1 onto `topologies.TOPOLOGIES["balanced"]` |
| 4. Cap = code, one constant | `gpu_solve_task.max_retries = CONFIG.repair_cap` (spike-proven). The composed graph cannot breach the cap because the cap lives in the TASK, not the graph |
| 5. `.rec` at op boundary, append-only per tier | Each task writes its tier's `.rec` via the existing `@recorded` decorator; pipe-path parity tested in Slice 1 and Slice 4 |
| 6. No streaming dependence | Canvas path returns final results only; workers stream from Ollama internally but tasks return completed text |

## Phase-0 decision gate (when does Phase 2 happen?)

Phase 2 (`low_latency` chord, topology selector at the agent boundary,
hardware pinning) starts only when a topology beats the hardcoded cascade on a
real metric:

- `low_latency` P50 via speculation (`group(draft, generate)` chord), OR
- `low_power` joules/task once per-task energy accounting lands.

Slice 4's findings doc establishes parity (necessary condition) but does not
satisfy this gate (sufficient condition). The gate is a separate decision
after Phase 1 lands.
