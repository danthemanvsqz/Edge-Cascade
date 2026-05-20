# Edge-Cascade observability + tuning — multi-session backlog

> **This is the canonical, status-tracked backlog.** Any fresh session
> picking this up cold: read the Cross-session protocol first, then pick the
> next `[ ]` item whose dependencies are all `[x]`. Update statuses here via
> a small commit per item; commit SHAs back-reference each item's work.

## Context

The 07:46–07:49 mesh-validation telemetry surfaced concrete observability
gaps; we also have the deferred Phase-2/3 tuning roadmap from the original
logfmt/repair work. This program unifies both into a single backlog any fresh
session can pick up cold, with grounded entry/exit per item so progress
survives session boundaries.

## Cross-session protocol (read first when picking up cold)

1. **Canonical backlog:** this file. Update statuses here.
2. **Status notation per item:** `[ ]` not started · `[~] <branch>` in
   progress · `[x] <SHA>` done. Updates land via a one-line edit + commit.
3. **Branching:** one focused branch per item, named `feat/obs-<id>`, based on
   `feat/phase1-logfmt-repair` (canonical bytes-native tooling; do NOT base on
   `main` until Phase-1 merges — `replay.load_streams` is bytes-only on the
   corrected core, `str`-based on stale main). One small PR per item.
   Exception: **A4** (Vinyl run) happens on `feat/embed-vinyl-subtree`, which
   already carries Phase-1 + the guardrail.
4. **Pick-up rule:** read this file; pick the next `[ ]` item whose
   dependencies are all `[x]`; execute per its spec; update status;
   commit/push/PR.
5. **Evidence discipline (non-negotiable):** `runs/*.rec` is ephemeral and
   gets overwritten by the next run. Immediately after any mesh/Vinyl run,
   `python scripts/snapshot_evidence.py --latest 1` and commit
   `evidence/<UTC-date>/`. This is the only way telemetry survives. We
   already lost the M3-run data for not doing this.

---

## Backlog (sequenced)

### A3 — Run-scoped evidence snapshot **(START HERE — foundational)**

**Why:** the prior M3-run's per-server `.rec` was overwritten and lost. No
evidence persistence ⇒ Part-C is impossible. Tiny, unblocks everything else.

**Files (new):** `scripts/snapshot_evidence.py`. Reuses
`replay.tag_and_merge / split_episodes / episode_summary` and
`dashboard.compute_metrics`.

**Approach:** CLI `--latest [N]` / `--since <ts>` / `--episode <i>`. Loads
`runs/*.rec` via `load_streams` (bytes), selects the target episode(s),
writes `evidence/<UTC-date>/{replay.json, dashboard.json, MANIFEST.md}`.
MANIFEST: run_ids, episode bounds, edge-cascade HEAD SHA + `projects/vinyl`
HEAD SHA (if present), spend assertion (`edge-cloud calls=0 / $0.00`), tool
counts. Idempotent (suffix `-N` on dup). Offline; no model calls.

**Verification:** unit tests for selection; smoke against current 07:49
telemetry → produces a valid `evidence/2026-05-19/` parseable by `jq`.
`spend.clean=true`.

**Exit:** working script + smoke evidence dir committed.
**Dependencies:** none. **Branch:** `feat/obs-snapshot`. **Status:** `[x] f712c82` (smoke artifact: `evidence/2026-05-20/`)

---

### A1 — Repair-round policy + dashboard metric truth

**Why (grounded):** today's run shows `repair_round_hist {"3": 1}`,
`cap_hits: 0`, `final_tier: {gpu: 1}` — a **3-round** repair loop that
*eventually passed*. `dashboard._final_tier` returns `"gpu"` whenever the
last `verify_functional` passed, **bypassing** the `repairs >= 2 →
capped->tier3` branch. So a policy-violating-but-passing over-cap loop is
invisible in the dashboard. RUNBOOK is explicit: 2 local rounds → Tier-3.

**Files:** `dashboard.py` (escalations block: add `over_cap_episodes`
counted independent of pass/fail; render red if >0);
`tests/test_dashboard.py` (new test: 3 repair rounds + final pass →
`over_cap_episodes=1`, `cap_hits=0`, `final_tier={gpu:1}`); `RUNBOOK.md` +
`CLAUDE.md` (tighten wording so a session cannot ambiguously do round 3:
*"If round 2 fails, escalate to Tier 3 — DO NOT begin round 3."*).

**Approach:** `REPAIR_CAP_MAX = 2` constant in dashboard;
`over_cap_episodes = sum(rounds > REPAIR_CAP_MAX for ep in episodes)`. Metric
counts policy breach regardless of outcome — that's the whole point.

**Verification:** pytest test_dashboard new case green; recomputing today's
metrics yields `over_cap_episodes=1`.

**Exit:** breach visible in dashboard; doc + metric agree.
**Dependencies:** none. **Branch:** `feat/obs-repair-cap-truth`. **Status:** `[x] 41758bb` (recomputed against the 00:07 telemetry → `over_cap_episodes=1`)

---

### A2 — NPU cold-start vs steady-state latency

**Why (grounded):** edge-npu p95 = 21.1 s vs p50 = 4.5 s; the 21 s is the
documented one-time vpux compile (RUNBOOK §0). The dashboard's per-server
percentiles include it → reads as "NPU slow" when steady-state is fine.

**Files:** `dashboard.py` (per_server latency: group by `run_id`, separate
first model-touching call into `cold_ms_list`; steady-state p50/p95/max
exclude it); render (add `cold` column); `tests/test_dashboard.py`.

**Approach:** within each (server, run_id) cohort, the first non-`status`
record's latency goes to `cold_ms`; remainder feed `p50/p95/max`.
Cold-start surfaced as a separate field (`cold_p95`, `cold_max`).

**Verification:** synth records (one large first-route per run_id + many
small drafts) → `cold_p95 ≈ large`, `p95 ≈ small`. On today's telemetry:
edge-npu `p95` drops to single-digit seconds; `cold_max ≈ 21s`.

**Exit:** dashboard distinguishes cold from steady-state.
**Dependencies:** none (independent of A1; can run in parallel).
**Branch:** `feat/obs-cold-start-split`. **Status:** `[ ]`

---

### A4 — Vinyl M3→M4 construction run (launched session — user-driven)

**Why:** M3 is in. M4 (htmx-ws spike → WS transport + OOB framing) is the
next milestone *and* the first true test of `projects/vinyl/CLAUDE.md`'s
anti-conflation guardrail in production.

**Preflight (must all be true):**
- A3 `[x]` (so the run's evidence is captured before next run clobbers `.rec`).
- On `feat/embed-vinyl-subtree`; preflight per RUNBOOK §0 (3 MCP servers,
  no `edge-cloud`); first `edge-npu` call compiles NPU (~12–21 s, expected).

**Launch:**
```
powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 `
  -ProjectDir C:\Users\danth\src\edge-cascade\projects\vinyl
```
Session follows `projects/vinyl/PLAN.md` (M3 done → htmx-ws spike → M4)
**under** `projects/vinyl/CLAUDE.md` (TS gate ONLY; never `../..`; never
host config). Gate per milestone: `npm run test:run` + `npm run typecheck` +
`npm run lint`.

**Exit (after the launched session ends):**
1. `scripts/snapshot_evidence.py --latest 1` (per A3) → commit
   `evidence/<date>/`.
2. M4 commit in `projects/vinyl`; spike findings in
   `projects/vinyl/ARCHITECTURE.md`.
3. PR `feat/embed-vinyl-subtree`.

**Dependencies:** A3 `[x]`. **Branch:** `feat/embed-vinyl-subtree` (existing).
**Status:** `[ ]`

---

### A5 — Router signal-value study (opportunistic, data-gathering)

**Why (grounded):** route() rated 4/4 tasks `standard` today, including the
trivial `add(a,b)`. RUNBOOK already notes the 1.5B miscalibration; this
gives it a data-anchored decision path instead of staying anecdotal.

**Data phase:** aggregate `producing.route_categories` across
`evidence/*/dashboard.json` over N ≥ 5 real runs (A4 + subsequent). If
entropy stays near zero, the deterministic short-circuit (long-deferred) is
justified.

**Action phase (conditional):** add a pre-route trivial-task filter to
`cascade/orchestrator.py:run_pipeline` (length / no-code-requested /
fenced-Python-only heuristics) → skip route(), enter Tier-1 directly.
Optional small extension to `npu_worker.route`.

**Verification:** decision-distribution table across the snapshots; if
shipping the short-circuit, a regression test for the bypass path.

**Exit:** either a written "router calibrated, no change needed" note (with
the data) OR the short-circuit shipped.
**Dependencies:** A3 `[x]`; ≥ 5 evidence snapshots accumulated.
**Branch:** `feat/obs-router-study`. **Status:** `[ ]`

---

### P2a — Incremental `.rec` tail-parse

**Why:** Phase-1 roadmap. `.rec` is append-only + length-framed; readers
re-parse the full file each load. Cost grows with history forever.

**Files:** `cascade/logfmt.py` (extend `parse_stream` with optional
`start_offset` → returns `(records, next_offset)`; keep backward-compat
default behavior); `replay.load_streams` (per-stream offset cache, with
shrink-→-reparse guard for rotation); `tests/test_logfmt.py` (offset
round-trip; tail-parse equivalence with full parse; rotation/shrink case);
`bench/bench_incremental.py` (new) showing O(new bytes) not O(total).

**Verification:** pytest green; logfmt branch coverage stays 100%; bench
shows tail-parse cost flat as history grows.

**Exit:** working incremental path.
**Dependencies:** none. **Branch:** `feat/obs-incremental-parse`. **Status:** `[ ]`

---

### P2b — Energy / joules accounting (gates P2c)

**Why:** explicit user requirement from the original Phase-2 plan — per-Hop
GPU joules must be **visible** before any speculative-discard tuning.
Without it, we can't see what "wasted" speculative work actually costs
(battery).

**Files (new):** `cascade/power.py` — pure
`joules_during(t0, t_end, sample_hz=4) → float` using `pynvml` if importable,
`nvidia-smi --query-gpu=power.draw` subprocess fallback. **Edits:**
`cascade/gpu_worker.py` (wrap `_generate` with power-sampler context manager
→ return `joules` on the result); `cascade/orchestrator.py:Hop`
(`joules: float = 0.0`, `discarded: bool = False`); .rec trace field schema
(extend with `j=<float>|d=<0|1>` per hop, additive); `dashboard.py` new
ENERGY panel (total joules, per-tier, % discarded); tests.

**Verification:** unit tests with mocked NVML; dashboard ENERGY panel shows
a non-zero joules number on a real GPU call; baseline `joules per task`
captured in an A3 snapshot.

**Exit:** ENERGY panel live; baseline number in `evidence/<date>/`.
**Dependencies:** A2 `[x]` (so steady-state latency is already partitioned;
joules attribution is cleaner). **Branch:** `feat/obs-energy-accounting`.
**Status:** `[ ]`

---

### P2c — Speculative GPU generate (gated on P2b live + visible)

**Why:** original Phase-2 architectural payoff. Submit `gpu.generate(query)`
concurrently with `npu.route()`; resolve on route outcome. Local-tier
speculation only (cloud stays strictly reactive + budget-gated).

**Files:** `cascade/orchestrator.py:run_pipeline` (futures wrapper — submit
GPU before awaiting route; on route → cloud or NPU-pass, abandon the future
and book the joules as `discarded`; on route → GPU, await the in-flight
future); `cascade/gpu_worker.py` (no true cancellation — Ollama keeps
generating; we just stop awaiting and book the joules); tests for both
branches.

**Verification:** ENERGY panel shows non-zero `discarded` joules when an
NPU draft passes; episode-latency P50 on the GPU branch drops measurably
vs pre-speculation baseline.

**Exit:** speculation live; both branches tested; discarded joules visible.
**Dependencies:** P2b `[x]` (the user-mandated visibility gate).
**Branch:** `feat/obs-spec-gpu`. **Status:** `[ ]`

---

### P3 — Stateful `repair_session` (architectural; pairs with A1)

**Why:** original Phase-3 item. Stateless `gen(prompt)` reprocesses the
invariant prefix (task + program) every repair round. Hold an Ollama chat
session across rounds with `keep_alive`; KV-cache the prefix. O(rounds ×
program) → O(program + rounds × delta) for prompt processing.

**Files (new):** `cascade/repair_session.py` — context manager mirroring
`cascade_session`; the session lifetime owns the Ollama chat handle.
**Edits:** `cascade/gpu_worker.py` (chat endpoint with conversation
handle); `validate_log.repair` (use the session); tests.

**Verification:** unit tests for lifecycle; benchmark showing per-round
prompt processing drops with growing program size.

**Exit:** stateful path used by `repair`; benchmark in evidence.
**Dependencies:** A1 `[x]` (cap/policy semantics settled first).
**Branch:** `feat/obs-stateful-repair`. **Status:** `[ ]`

---

## Recommended session sequencing

| Session | Item(s)           | Why |
|--------:|-------------------|-----|
| 1       | A3                | Tiny, foundational, unblocks all evidence. |
| 1 or 2  | A1, A2 (parallel) | Independent dashboard truth fixes; make the dashboard trustworthy before any tuning claim. |
| 2+      | **A4** (user-led) | The headline progress + first guardrail test; gated only by A3. |
| 3       | P2a               | Independent observability infra; small payoff but compounds. |
| 4–5     | P2b               | Bigger; user-mandated prereq for P2c. |
| 5+      | P2c               | Biggest architectural change; only after P2b. |
| any     | P3                | After A1 settles repair-cap semantics. |
| ongoing | A5                | Decided by data accumulated in A3 snapshots. |

## Verification across the program

Every item ships with: unit tests + bench (where applicable), an evidence
snapshot demonstrating the new behavior/metric, and a green pytest run
(`cascade.logfmt` at 100% branch). A4 ships its evidence as the proof of the
guardrail and of zero spend.

## Critical paths (canonical refs)

- Hardened parse/write: `cascade/logfmt.py`, `mcp_servers/_rec.py`,
  `cascade/orchestrator.py:write_record`.
- Observability core (most A/P items land here):
  `dashboard.py:compute_metrics`, `dashboard.py:_final_tier`,
  `replay.py:tag_and_merge / split_episodes / episode_summary`.
- Vinyl build target + guardrail: `projects/vinyl/PLAN.md`,
  `projects/vinyl/CLAUDE.md`.
- Run harness + policy: `scripts/edge-cli.ps1`, `CLAUDE.md`, `RUNBOOK.md`.
