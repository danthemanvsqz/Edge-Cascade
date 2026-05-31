# Backlog (groomed)

Live, prioritized backlog. Ordering and zones follow
[PRIORITIZATION.md](PRIORITIZATION.md) — a 4×4 **Impact × Severity** matrix:
impact descending, then severity ascending (safest first); the `I1` column is
dropped, the `S4` row is parked + de-risked.

> Last groomed: **2026-05-30** (after Phase 2 Slice 6 merged; main `ed7588c`).
> Updated **2026-05-30 (eve)**: added **★ #6 live cascade-activity tool (OBS-1)**.
> Updated **2026-05-30 (late)**: **#6 OBS-1 SHIPPED** (PRs #109–#113: the
> Flower-backed live lane + event-receiver push + the dashboard spinning ring).
> Added the **self-healing arc #7–#11** from the 2026-05-30 routing-log analysis
> (24 routes this session; see the section below for the evidence behind each).
> Updated **2026-05-31**: **#9 draft_gate-decompose SHIPPED** (PR #119, part of
> the big refactor/canvas commit — split `_balanced_draft_gate` into `_verify` +
> `_resolve_npu`; BACKLOG.md wasn't updated at merge time). Next pick = **#1 PT-1**.

## Current placement

```
 Severity ↓ \ Impact →   I1 Trivial   I2 Minor          I3 Major                       I4 Critical
 S1 Safe                  ✗ (none)     #11 hook-scope    #1 PT-1 ← NEXT                 — (none)
                                                         #12 obs-legibility ✅DONE
 S2 Low                   ✗ (none)     #4 gate-helper    #7 ts-verify-gate ✅DONE        — (none)
                                       #5 PT-4 verbump   #2 PT-2 (Slice 7: blocked)
                                       #13 nonblock-hold*  *v2 of #12
 S3 Moderate              ✗ (none)     —                 #3 PT-3                         — (none)
                                                         #8 difficulty-recal ✅DONE
                                                         #9 draft_gate-decompose ✅DONE
 S4 Severe (park)         ✗ (none)     ⏳ none           ⏳ none                         — (none)
```

No `S4` park items and no `I1` drops right now. Slice 7 is **dependency-blocked**
(not parked) — see below. **Shipped:** #6 OBS-1, #7 ts-verify-gate (PR #115),
#8 difficulty-recal (PR #116), #12 obs-legibility (PR #117: the min-lit hold +
NPU-gave-up counter), **#9 draft_gate-decompose (PR #119**: split
`_balanced_draft_gate` → `_verify` + `_resolve_npu`; BACKLOG update missed at
merge time). #10 ts-shortcut is retired (superseded by #7). **The next pick is
#1 PT-1** — confirm full GPU offload (I3·S1, highest-impact + safest). **#13
nonblock-hold** is the v2 of #12 (the receiver's min-lit `sleep` blocks the event
thread; a non-blocking scheduler is the principled fix — Opus review of PR #117).

---

## Self-healing arc #7–#11 (from the 2026-05-30 routing-log analysis)

**Evidence base** — 24 routed outcomes this session (`runs/cascade.rec`,
session-scoped by a 55-min ts gap): **18W / 6L (75%)**; final_tier `npu 2 · gpu
16 · capped→tier3 6`; **11/24 (46%) skipped the NPU draft** (difficulty ≥ 0.7);
routing wall-time 13.1 min, of which the **6 caps consumed ~5.5 min (42%) for
zero usable output**. Language split: **TypeScript 0/3 won (0%)** vs
Python/algo **18/21 (86%)**.

### ✅ #7 · ts-verify-gate — a TS backend for the deterministic gate  (I3 · S2) — SHIPPED (PR #115)
**Done:** `cascade/ts_verifier.py` + `dashboard/scripts/ts-syntax-check.mjs`
(single-file `ts.transpileModule` syntax check, parity with the Python AST gate)
+ `_gate` language dispatch. Live-verified: a TS task that capped 100% before now
wins @ npu; Python parity intact. Original analysis retained below.

**The gap:** all **3 TS routes capped (100%)**. The locals *draft* TS fine; the
`edge-verify` gate is Python-only ([[edge-verify-ts-gap]]), so every TS draft
fails gating and burns both GPU repair rounds before `capped→tier3`. **The gate,
not the model, is the wall.** Wire `tsc + eslint + vitest` as a verify backend
keyed on `.ts`/language so TS drafts can actually PASS.
**Why I3:** turns a structurally-0%-win lane winnable AND reclaims ~100s/session
of guaranteed-cap GPU time; it's the linchpin of the whole arc. **Why S2:**
additive — a new verify backend behind the existing gate interface; the Python
gate path is untouched, fully reversible. Highest-leverage pick.

### #8 · difficulty-recal — the NPU router over-rates short prompts  (I3 · S3)
**The gap:** **11/24** routes scored ≥ 0.7 and skipped the NPU draft straight to
GPU — but several were trivial single functions (e.g. "Write a single Python
function `snapshot()`" scored **0.85**). Of those eleven 0.85s: 9 GPU wins, 2
caps — and a chunk of the GPU wins were cheap enough the NPU likely could have
taken them. Over-rating pushes work up a tier ($ + latency) needlessly. Length-
correct or recalibrate the `≥0.7 ⇒ skip-draft` threshold.
**Why I3:** affects every route's tier selection (cost/latency lever). **Why
S3:** touches the router's difficulty signal — mis-calibration risks drafting a
genuinely-hard task at NPU (a wasted round); guarded by the win/lose metric +
parity tests, measure before/after on `cascade.rec`.

### #9 · draft_gate-decompose — split the overloaded gate node  (I3 · S3)
**The gap:** `mesh.balanced._draft_gate` is ONE node doing **three** jobs behind
**two** verifiers: it (a) *verifies* via `_gate()` (syntax **or** functional
engine), (b) *resolves* on PASS (`final_tier="npu"`, `resolved`), and (c)
*escalate-routes* on FAIL (carry `failures` → GPU). The `verify` queue also runs
`_done`, so the dashboard's single node conflates gating with final logging.
Decompose into distinct chain nodes: **verify** (pure pass/fail) → **resolve**
(finalize npu win) | **escalate** (carry to GPU).
**Why I3:** the live ring (#6) gives per-node meaning only if a node is one
responsibility; this also decouples verification from routing policy so either
can change/reuse independently. **Why S3:** a refactor of the hot-path Canvas
chain — the cap invariant + the pipe-parity contract
([FINDINGS-canvas-phase1.md]) must hold across the split; eager-test the new
composition before it lands.

### ~~#10 · ts-shortcut~~ — RETIRED (superseded by #7)  (I2 · S2)
**Dropped:** #7 shipped, so the gate now wins TS instead of guaranteed-capping
it — the stopgap has no remaining purpose. Kept for the record only.

(original) — hand TS straight to Tier 3 until #7 lands:
**The gap (stopgap):** while the gate can't certify TS (#7), a TS task is a
*guaranteed* cap — ~35s of draft + 2 GPU rounds for nothing. Detect TS and route
it directly to `capped→tier3` (still logged), skipping the futile draft/repair.
**Why I2:** saves ~35s/TS route but is a workaround, and goes **dead the moment
#7 lands** — schedule behind #7, drop if #7 ships first. **Why S2:** a small
routing branch on the language signal, reversible.

### #11 · hook-scope — session-scope the advisory scoreboard  (I2 · S1)
**The gap:** `pipeline_reminder.py` reports **all-time** metrics ("37 routed,
20W/8L"), so a strong session (this one: 18W/6L, 75%) is diluted by history and
W+L ≠ total (9 older records lack a `done:` trace line). Show *this session's*
W/L alongside all-time (borrow the dashboard's `START_FROM_EOF` session-coupling).
**Why I2:** observability nicety, sharpens the nudge. **Why S1:** additive to an
advisory hook that already degrades to silence on any error — cannot break prompt
submission. Quick surgical win.

---

## ✅ #6 — live cascade-activity tool (OBS-1)  (I3 · S2) — **SHIPPED 2026-05-30**

**Done** (PRs #109–#113): `cascade/flower_activity.py` (Flower-backed probe) +
`cascade_top` debug view + `sample_occupancy` + the event-receiver push producer
(`cascade/live_receiver.py` / `scripts/cascade_live_receiver.py`) + the
dashboard spinning ring on its own `cascade-spin` live region (event-driven push,
no polling) + the `tool=status` probe filter + `docs/DESIGN-observability-lanes.md`.
The original design notes are retained below for history.

**The gap (found this session):** the dashboard can't show *which node is
currently spinning* because `.rec` records are written at task **completion**,
not start. While `gpu_solve` actually grinds (~60s on a hard task), zero records
exist, so the node only blips "hot" for a couple seconds *after* each generate
finishes — there is no in-progress signal in the `.rec` stream. Confirmed live:
a red-black-tree solve ran `gpu_solve` ~60s with the node dark the whole time.

**The data already exists natively — no Flower needed.** Celery's
`app.control.inspect().active()` returns the currently-executing task per worker
in real time (verified this session: `{'celery@Alienware': []}` idle; it lists
the running task during a solve). `task_track_started=True` is already set in
`cascade/celery_app.py`. A ~60-line module over `inspect.active()` is more
decoupled and reusable than wrapping Flower's HTTP API; Flower stays an optional
heavyweight UI, not a dependency.

**Design — a DECOUPLED tool, not a dashboard feature** (user directive: one
source of truth usable by the UI, debugging, AND experimentation):

`cascade/live_activity.py` — thin, read-only probe (no broker writes; safe to
call from anywhere):

```python
@dataclass(frozen=True)
class ActiveTask:
    task_name: str   # "mesh.balanced._gpu_solve"
    node: str        # "gpu_solve"  (mapped chain-node id)
    tier: str        # "gpu"
    task_id: str
    worker: str
    runtime_s: float

def snapshot(timeout=2.0) -> list[ActiveTask]   # running tasks, all workers
def active_nodes(snap) -> set[str]              # {"gpu_solve"}
NODE_BY_TASK: dict[str, str]                    # celery task name -> node id
```

Three thin consumers on top (each just imports or fetches the tool):
1. **Debugging** — `scripts/cascade_top.py`: a live `top`-style view of active
   cascade tasks (~2 Hz refresh).
2. **Experimentation** — experiments `import snapshot()` / `active_nodes()` to
   measure tier occupancy + timings.
3. **UI** — a tiny JSON endpoint `GET /active` → `snapshot()`; the Node
   dashboard polls it and `dashboard/src/flow.ts` renders a **spinning ring** on
   the active node (distinct from the post-completion "hot" blip added this
   session). Transport choice **A (HTTP/JSON)** so the same endpoint also serves
   curl-debugging + experiments.

**Build order:** module + debug CLI first (the decoupled core, immediately
useful for debug/experiments), then the JSON endpoint + the UI spinning ring.

**Route-first:** from-scratch build → through the pipeline (route → GPU draft
for the self-contained snapshot/mapping logic → Tier-3 for the Celery-integration
glue), per the route-every-coding-task rule.

**Acceptance:**
- `snapshot()` returns the running task during a live solve; its mapped node
  matches the chain step actually executing.
- `cascade_top.py` shows `gpu_solve` lit for the *whole* GPU phase, not a blip.
- Dashboard spinning ring tracks the active node live (`gpu_solve` spins the
  full ~60s of a hard solve).

**Why I3·S2:** Major — one reusable observability source unblocks the live
"spinning node" UI *plus* debugging *plus* experiment instrumentation
(multi-consumer payoff). Not I4 (the cascade runs fine without it). S2 —
additive, read-only `inspect.active()`, touches neither the hot path nor the
repair/self-heal loop, needs no worker config change (`-E` not required),
fully reversible.

---

## #1–#3 — llama_cpp performance-tuning sub-arc  (I3 · Major)

**Why I3:** this is the linchpin that unblocks **Slice 7** (flip the GPU backend
default `ollama → llama_cpp` and drop the Ollama daemon dependency) — the payoff
of the whole Phase-2 direct-loading arc (collapse the HTTP hop, per-model VRAM
control). Not `I4`: the cascade works today on Ollama and latency is a tiebreaker
metric, not a top one ([PRIORITIZATION.md]/metric-priorities). Not `I2`: it gates
a whole planned slice + the direct-loading thesis.

**The gap (Slice 2 / PR #95,
[FINDINGS-celery-phase2-parity.md](FINDINGS-celery-phase2-parity.md)):** functional
parity holds, but `llama_cpp` is **3.4× slower** than Ollama on GPU-heavy cases at
the current config (Case B: 110.4s vs 32.1s; Case C: 136.0s vs 40.5s). Same GGUF
blob is loaded on both backends, so the gap is **configuration**, not the model.

**Measurement harness (existing):** `scripts/parity_batch.py --backend
<ollama|llama_cpp>` writes `runs/parity-canvas-<backend>.json` with per-case wall
times. Re-run Case B/C after each tuning change; the bar is the Slice-2 criterion
(**within ±20% of Ollama's steady-state**). Needs the live GPU + CUDA
`llama-cpp-python` wheel — not a CI/eager change.

Current config in [cascade/llama_worker.py](../cascade/llama_worker.py)
(`make_llama_worker`): `n_gpu_layers=-1`, `n_ctx=8192`, `verbose=False`, no
`flash_attn`/`n_batch` tuning; `_generate` issues a fresh `create_chat_completion`
per call (re-prefills the system prompt every time).

Decomposed levers, in priority order:

### #1 · PT-1 — confirm full GPU offload  (I3 · S1)
The highest-suspicion, lowest-risk check first. Verify `n_gpu_layers=-1` actually
offloads **all** qwen14b layers (turn on the load log / count offloaded vs total).
If layers spill to CPU under VRAM pressure at `n_ctx=8192`, that alone could
explain most of the 3.4×. Pure diagnostic — no behavior change, fully reversible.

### #2 · PT-2 — context + attention config sweep  (I3 · S2)
Low-risk, reversible config, each lever measured independently:
- `n_ctx` `8192 → 4096` (Ollama sizes context dynamically to the prompt; a fixed
  8192 allocates a large KV cache = VRAM + per-token attention cost). Frees VRAM,
  which also helps PT-1.
- `flash_attn=True` (Ollama enables it by default for supported models).
- tune `n_batch` (prefill throughput; default 512).

The functional-parity gate (`parity_batch`) guards against any output change.

### #3 · PT-3 — KV-cache / system-prompt prefix reuse  (I3 · S3)
`_generate` re-prefills the `_SYSTEM` prompt on every call; Ollama's daemon keeps
the session/KV warm. Reuse the prefix KV across calls in the resident worker
(prompt caching). Higher severity: touches the call pattern and risks chat-template
/ correctness drift if prefix caching is mishandled — guarded by the parity gate.

**Decision gate (after PT-1..PT-3):** does steady-state Case B/C wall come within
±20% of Ollama? **Yes →** unblock Slice 7 (flip default). **Plateaus (e.g. ~1.5×) →**
explicit choice: accept the gap and flip anyway for the direct-loading benefits, or
keep Slice 7 parked + Ollama default. Either way record it in the parity findings.

---

## #4 — extract `_pick_first_verified` gating into a covered helper  (I2 · S2)
Reviewer idea from PR #103. The low_latency chord's gating *decision*
(cheapest-first / unavailable-skip / double-miss-cap) is product logic living in the
coverage-omitted celery substrate (`cascade/topologies_canvas.py`). It's
behaviorally tested by the 7 eager cases but not under the 100% gate. Extract the
pure decision into a non-omitted helper so the gate covers it. Minor impact (logic
*is* tested), small safe refactor.

## #5 — llama-cpp-python version check  (I2 · S2)
On `0.3.23`; check upstream for perf changes since the cu124 wheel was cut and bump
if it helps. Minor/maintenance; a bump can shift the CUDA wheel + runtime-DLL setup
([FINDINGS-celery-phase2-parity.md] side-finding), so measure before keeping.

---

## Blocked (dependency, not parked)

- **Slice 7 — default flip `ollama → llama_cpp` + Ollama deprecation**  (I3 · S2).
  A one-line `CONFIG.gpu_backend` default change + deprecation docs — *not* risky,
  so not an `S4` park. **Blocked on the PT decision gate above.** Re-enters the
  actionable order at `I3·S2` once perf comes within the bar (or the gate decides to
  flip despite a residual gap).

## User-owned (needs the hardware, not agent engineering work)

- **low_latency vs balanced wall-time comparison** — fill the TBD table in
  [FINDINGS-canvas-phase2-low-latency.md](FINDINGS-canvas-phase2-low-latency.md) by
  running `scripts/mesh_solve_canvas.py --topology {balanced,low_latency}` on the
  NPU + RTX + Redis box, then settle the Phase-0 decision gate for low_latency.
