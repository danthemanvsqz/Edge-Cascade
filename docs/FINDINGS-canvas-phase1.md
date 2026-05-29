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

## Results

**Pending — run on the user's hardware (NPU + RTX 5070 Ti) and the local
Redis container.** Methodology and CLI committed in this slice so the
runner doesn't need to re-derive the protocol.

### Case A — NPU gate PASS

| Path | Wall time (s) | final_tier | repair_rounds | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|
| `cli.py` (pipe) | TBD | TBD | TBD | TBD | TBD | TBD |
| `mesh_solve_canvas.py` (Canvas) | TBD | TBD | TBD | TBD | TBD | TBD |

### Case B — GPU first attempt PASS

| Path | Wall time (s) | final_tier | repair_rounds | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|
| `cli.py` (pipe) | TBD | TBD | TBD | TBD | TBD | TBD |
| `mesh_solve_canvas.py` (Canvas) | TBD | TBD | TBD | TBD | TBD | TBD |

### Case C — Cap → Tier-3 (cloud disabled)

| Path | Wall time (s) | final_tier | repair_rounds | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ | `edge-cloud.rec` Δ |
|---|---|---|---|---|---|---|---|
| `cli.py` (pipe) | TBD | TBD | TBD | TBD | TBD | TBD | 0 (expected) |
| `mesh_solve_canvas.py` (Canvas) | TBD | TBD | TBD | TBD | TBD | TBD | 0 (expected — no `cloud` worker) |

### Reproduce

After running both CLIs on each prompt, fill the tables above by replacing
each TBD with the captured value. The `.rec` deltas can be derived from a
pre/post `wc -c < runs/edge-*.rec` snapshot; the `mesh.Outcome` fields are
in both CLIs' stdout.

## Verdict

**Pending live results.** Eager-mode parity is already pinned by Slice 3's
test suite; the live-broker proof closes the eager-≠-broker seam the spike
flagged. If results match within the pass criteria → Canvas substrate is
parity-equivalent to the pipe path on a single box, and the Phase-0
decision gate (does a topology beat the hardcoded cascade on a real
metric?) becomes the next Phase 2 question. If results diverge → file the
divergence as a Slice-4-follow-up and gate Phase 2 on it.

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
