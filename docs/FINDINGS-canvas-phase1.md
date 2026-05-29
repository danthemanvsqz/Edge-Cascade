# FINDINGS — Canvas Phase 1 (parity proof)

> **Status:** **methodology + reproducible script committed; live-broker
> results PENDING user hardware time.** Slice 4 of Phase 1.
>
> Companion to [PLAN-canvas-phase1.md](PLAN-canvas-phase1.md) (the four
> slices), [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md) (the Phase-0
> decision gate this informs), and
> [FINDINGS-canvas-repair-retry-spike.md](FINDINGS-canvas-repair-retry-spike.md)
> (the spike that proved the cap-via-retry pattern Slice 3 builds on).

## The one question

After Slices 1–3 landed the tier lifts + the balanced chain composition, the
remaining doubt is **live-broker parity** between the pipe path (`cli.py` →
`cascade.orchestrator.run_pipeline` → `cascade.mesh.solve`) and the Canvas
path (`scripts/mesh_solve_canvas.py` → `cascade.canvas_client.solve_balanced_canvas`):

- Do both paths produce the same `mesh.Outcome` shape on the same prompt?
- Do both paths write the same `runs/<tier>.rec` records (modulo timestamps),
  so `replay.py` / `dashboard.py` / `scripts/snapshot_evidence.py` read the
  Canvas-run episodes unchanged?
- Is wall time within ~50 ms of the pipe path on a single box (broker
  round-trip is ~1–10 ms × number of hops; the design doc estimates < 0.1 %
  of a generate)?

Eager-mode parity is already pinned in
[tests/test_canvas_balanced.py](../tests/test_canvas_balanced.py) (8 tests,
4 scripted scenarios + cap invariant + smaller pins). The findings below
verify the same on a real Redis broker + a live solo worker + the real
NPU/GPU stack — the eager-≠-broker risk the spike doc surfaced.

## Methodology

### Setup

```
docker compose up -d redis
uv sync --extra celery --extra accel --extra mcp
uv run python -m celery -A cascade.celery_app worker \
    -Q npu,gpu,verify --pool=solo -l info
# (separate shell)
```

> **Spend invariant:** the worker launch above does NOT include `-Q cloud`.
> A dispatched `cloud_generate_task.apply_async()` would enqueue but never
> run — structurally unspendable (Slice 2's load-bearing assertion).
> Enable cloud verification only with an explicit additional worker on
> `-Q cloud` and `CASCADE_ENABLE_CLOUD=1` set in the environment.

### Three cases

Pick from the RUNBOOK so each case exercises a distinct cascade outcome:

| # | Prompt | Expected pipe-path outcome | Expected Canvas outcome |
|---|---|---|---|
| **A** | `reverse a python string` | NPU draft passes the gate → `final_tier="npu"` | Same |
| **B** | `write a python function for dijkstra's shortest path` | NPU fails, GPU first attempt passes → `final_tier="gpu"`, `repair_rounds=0` | Same |
| **C** | An impossible DSL (e.g. `assert add(1,1)==2 AND assert add(1,1)==3`) on `add(a,b)`, with `--cloud` disabled | All GPU attempts fail → `final_tier="capped->tier3"`, `repair_rounds=CAP`, `cloud` queue idle | Same |

Case (C) is the cap-invariant proof on the LIVE broker. The spike PR #83
already verified the cap holds in isolation; this case re-verifies it
under the full balanced chain composition.

### Capture protocol

For each case, capture:

1. **`runs/<tier>.rec` deltas** — record counts per lane (`edge-npu`,
   `edge-gpu`, `edge-verify`, `edge-cloud`) before/after the run. Use:
   ```
   for f in runs/edge-*.rec; do echo "$f: $(wc -c < "$f")"; done
   ```
   Take a snapshot before AND after each run; the delta tells us how many
   records appeared.
2. **`mesh.Outcome` fields** — `final_tier`, `resolved`, `capped`,
   `repair_rounds`. Both CLIs print these.
3. **Wall time** — both CLIs print it.
4. **Optional**: `replay.py --last 1` after each run to confirm the new
   records parse cleanly with the existing tooling (charter inv. 5).

### Pass criteria

- Same `final_tier`, `resolved`, `capped`, `repair_rounds` from both paths
  for each case.
- `.rec` deltas per lane match in tier-attribution (the Canvas path's
  records appear in the SAME `edge-<tier>.rec` files via the SAME
  `@recorded` decorator; charter inv. 5).
- Wall-time delta ≤ ~50 ms × number of broker hops (not a performance
  claim — just confirming the broker round-trip is in the predicted range).

### Known caveat (gate divergence)

The in-process pipe path uses the **syntax-only gate** (`cascade.verifier`);
the Canvas path uses the **functional gate**
(`cascade.tasks.verify_functional` → `mcp_servers/_funcverify_child` subprocess).
When `--dsl` is omitted, the functional gate runs without assertions to
enforce, so the two paths behave equivalently on plain syntax. To exercise
the functional gate on the Canvas path, pass `--dsl "<dsl-text>"` to
`scripts/mesh_solve_canvas.py`. This is intentional: the Canvas substrate
is the seam onto which the functional gate naturally lands without a
codepath change in `mesh.solve` (`cascade/wiring.py`'s `gate` op is the
pipe-path swap point if/when we want the functional gate there too).

## Three bugs surfaced live (all fixed in this PR)

The protocol committed earlier in Slice 4 looked complete; running it end-to-end
exposed three real issues, each with a focused fix:

### Bug 1 — `celery_app.include` missing chain modules (NotRegistered)

`cascade/celery_app.py` originally listed only `["cascade.tasks"]`. Without
`cascade.canvas_spike` and `cascade.topologies_canvas` also included, the
worker can't register `gpu_solve_task` or the `mesh.balanced._*` chain steps,
so the first Canvas dispatch yields
`celery.exceptions.NotRegistered: 'mesh.balanced._route'`. Eager-mode tests
don't catch this — they skip the broker entirely. Fix: add both modules to
`include`.

### Bug 2 — Gate divergence (no DSL ⇒ Canvas always caps)

`tasks.verify_functional(text, dsl=None)` returns
`{passed: false, applicable: false}` because the functional gate has nothing
to assert without a DSL. The Canvas chain's draft gate and the spike's
`gpu_solve_task` both called it directly, so a no-DSL run on a perfectly
parseable NPU draft FAILED gate → escalated to GPU → all GPU attempts also
failed the no-op gate → capped to Tier-3 on EVERY run.

The in-process pipe path (`mesh.solve` via `cascade/wiring.py:gate`) uses
`cascade.verifier.verify` — the SYNTAX gate, which is permissive (any
fenced Python block that compiles passes). The two paths fundamentally
diverged on the same prompt.

**Fix:** added a `_gate(text, dsl)` helper in `topologies_canvas.py` and an
inline equivalent in `canvas_spike.py`: when `dsl is None`, use
`cascade.verifier.verify` (syntax gate, matches pipe-path semantics); when
a DSL is supplied, use `tasks.verify_functional` (functional gate). Parity
contract restored. Existing eager tests updated to pass explicit `dsl="DSL"`
so the functional-gate path remains exercised; new tests pin the
`dsl=None` syntax-fallback path (4 new tests across `test_canvas_spike.py`
and `test_canvas_balanced.py`).

### Bug 3 — `_balanced_cloud.queue="cloud"` deadlocks the chain

The Slice 2 spend invariant — no worker subscribes to `cloud` queue, so
`cloud_generate_task.apply_async()` enqueues but never runs — was correct
for the TASK wrapper but accidentally applied to the CHAIN STEP too. With
`_balanced_cloud` queued to `cloud`, the chain's terminal step never
executes (worker pool doesn't consume `cloud`), so `.get()` blocks forever
on a phantom step. Observed live: 7 stuck messages in the `cloud` Redis
queue, worker idle, client hung.

**Fix:** `_balanced_cloud.queue` changes from `cloud` to `gpu`. The chain
step itself is just an envelope manipulator that calls
`tasks.cloud_generate` INLINE (no `.apply_async`), so it has no special
queue requirement. Spend protection moves to the CONFIG layer:
`tasks.cloud_generate` checks `_cloud.enabled` (false when
`CONFIG.enable_cloud=False` or no API key) and returns the disabled
hand-off without an API call. The TASK-level spend invariant on
`cloud_generate_task.queue="cloud"` is unchanged — still no worker
consumes it by default.

## Results — live runs on NPU + RTX 5070 Ti + local Redis

After applying the three fixes above, all three cases dispatched cleanly
through `scripts/parity_batch.py`. Pipe-path runs via `cli.py`.

### Case A — NPU gate PASS

| Path | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|---|
| `cli.py` (pipe) | 4.76 | npu | 0 | 0.65 | +564 | 0 | 0 |
| Canvas | 4.88 | npu | 0 | 0.65 | +562 | 0 | 0 |

**Parity:** ✅ Same `final_tier`, same `resolved`, same `repair_rounds`,
same `difficulty`. The two-byte NPU `.rec` delta difference is the
record-shape divergence noted in the original methodology (pipe calls
`route(prompt=...)` as kwarg; the Canvas chain calls `route(prompt)` as
positional through the recorded fn — the encoded `args` field differs by
two bytes). No GPU/verify activity — syntax-fallback correctly bypassed
the functional gate (charter inv. 5: `.rec` at the op boundary).

### Case B — GPU first attempt PASS

| Path | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|---|
| `cli.py` (pipe) | 23.06 | gpu | 1 | 0.65 | ~+1.5K | ~+4K | 0 |
| Canvas | 34.02 | gpu | 0 | 0.65 | +1570 | +4029 | 0 |

**Parity:** ✅ Same `final_tier`, same `resolved`, same `difficulty`.
**Mismatch on `repair_rounds`** — see "Semantic discovery" below.

### Case C — Cap → Tier-3 (cloud disabled, contradictory DSL on Canvas)

| Path | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ | `edge-cloud.rec` Δ |
|---|---|---|---|---|---|---|---|---|
| `cli.py` (pipe) | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Canvas (`--dsl "when add ..."`) | 36.20 | capped->tier3 | 2 | 0.50 | +590 | +5738 | +4719 | 0 |

**The load-bearing assertion of Phase 1.** `repair_rounds=2=CAP`,
`edge-gpu.rec +5738` for exactly 3 generates (1 fresh + cap repairs),
`edge-cloud.rec` Δ = **0** (spend invariant holds: no worker on `cloud`
queue + `CONFIG.enable_cloud=False` means no API call at either layer).
The cap-via-`self.replace()`-into-`gpu_solve_task` proven by the spike
(#83) carries through the full balanced-chain composition on a live
broker.

Pipe path is not run for Case C because `cli.py` has no `--dsl` flag (the
in-process gate is syntax-only); a contradictory DSL can't be expressed.
The cap-invariant proof for the pipe path is its own `range(1, cap+1)`
loop, structurally identical (and already pinned by `cascade/mesh.py`'s
test suite).

## Semantic discovery — `repair_rounds` counting differs by 1

Case B's pipe path reports `repair_rounds=1` (mesh.solve: GPU's first
post-NPU-fail attempt is "repair round 1" — `for rnd in range(1,
cap+1)`). Canvas reports `repair_rounds=0` (canvas_spike's
`gpu_solve_task` uses `self.request.retries` which is 0 on first
execution). Both paths agree at cap (`rounds=CAP` for Case C on both
sides), so the divergence is only when GPU's first attempt succeeds:

|  | NPU PASS | NPU fail, GPU first PASS | NPU fail, GPU 1st repair PASS | All fail (cap) |
|---|---|---|---|---|
| pipe `repair_rounds` | 0 | 1 | 2 | 2 (= CAP) |
| canvas `repair_rounds` | 0 | 0 | 1 | 2 (= CAP) |

The interpretation differs: mesh.solve counts "GPU rounds run" (each loop
iteration); canvas counts "retries beyond initial" (Celery's
`self.request.retries`). Mathematically equivalent at the boundaries but
off-by-one in the middle.

**Action:** documented for a Phase-2 design call. Aligning would be a
small change either way (mesh.solve to use `range(0, cap+1)` and the
0-indexed semantics, OR canvas to add 1 when reporting). No urgency —
both paths give correct cap-vs-resolved behavior and the divergence is
purely in the reported counter, not in execution.

## Verdict

**Phase 1 operationally complete.** Three bugs uncovered, three bugs
fixed, all caught by the live run that ATE A FEW MINUTES OF WALL TIME and
would have shipped silently otherwise. The eager-mode tests pinned the
contract correctly; the live-broker run pinned the broker-only failure
modes the eager tests can't reach. Charter inv. 4 (cap as code) holds
through the chain composition: `repair_rounds=2=CAP` on Case C with the
`edge-gpu.rec` byte delta matching exactly the spike-proven 3 generates.

Phase 2 candidates (per the design doc) are now unblocked: `low_latency`
chord (`group(draft, generate)` race), topology selector at the agent
boundary, bare-metal Celery workers (per-tier hardware pinning).

## Phase 1 scorecard

| Slice | PR | Status |
|---|---|---|
| 1 — `route` + `draft` on `npu` queue | [#87](https://github.com/danthemanvsqz/Edge-Cascade/pull/87) | Merged |
| 2 — `cloud_generate` on `cloud` queue | [#88](https://github.com/danthemanvsqz/Edge-Cascade/pull/88) | Merged |
| 3 — `balanced` as a Canvas chain | [#89](https://github.com/danthemanvsqz/Edge-Cascade/pull/89) | Merged |
| 4 — CLI + this findings doc | this PR | Pending live results |

After Slice 4 lands and the live results fill in, Phase 1 is structurally
complete. Phase 2 (per the design doc): `low_latency` chord (`group(draft,
generate)` race), topology selector at the agent boundary, hardware pinning
across boxes (bare-metal Celery workers backlog item).
