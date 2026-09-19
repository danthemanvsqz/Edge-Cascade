# Backlog (groomed)

Live, prioritized backlog. Ordering and zones follow
[PRIORITIZATION.md](PRIORITIZATION.md) — a 4×4 **Impact × Severity** matrix:
impact descending, then severity ascending (safest first); the `I1` column is
dropped, the `S4` row is parked + de-risked.

> **Last groomed: 2026-09-19** — new arcs: **EDGE-1** (supervisor launch, HIGH PRIORITY),
> **MD** (deprecate the edge MCP servers) and **EXP-MR** (local model refresh).
> Prior: 2026-06-26 (SR-1 shipped as #144).

## Current placement

```
 Severity ↓ \ Impact →   I1 Trivial   I2 Minor                     I3 Major              I4 Critical
 S1 Safe                  ✗ (none)     MD-4 docs sweep              — (MD-1 → EDGE-1)     — (none)
 S2 Low                   ✗ (none)     MD-2 relocate shared →       EXP-MR-1 model        ★ EDGE-1 supervisor
                                          MD-3 delete servers          refresh (fits 12GB)     launch
 S3 Moderate              ✗ (none)     — (none)                     EXP-MR-2 MoE          — (none)
                                                                        offload arm
 S4 Severe (park)         ✗ (none)     ⏳ #5 PT-4 HOLD (re-probe)   — (none)              — (none)
```

**Pick order (impact ↓, then severity ↑):**
1. **★ EDGE-1** `edge` = one-command supervisor launch (I4·S2) — **HIGH PRIORITY**; subsumes MD-1
2. **EXP-MR-1** model refresh, 12 GB-resident candidates (I3·S2) — needs EDGE-1 (free VRAM, live substrate)
3. **EXP-MR-2** MoE partial-offload arm (I3·S3) — after EXP-MR-1's harness exists
4. **MD-4** docs sweep (I2·S1)
5. **MD-2** relocate shared modules out of `mcp_servers/` (I2·S2), then **MD-3** delete the servers (I2·S2)

**Parked:** #5 PT-4 (llama-cpp-python bump) — HOLD on AVX-512. **Next de-risk step
(new, 2026-09-19):** upstream is at **0.3.35** (2026-08-17); probe whether the cu12x
Windows wheel still requires AVX-512 (`--dry-run` install into a scratch venv + load one
GGUF). If not, PT-4 drops to S2 and re-enters at I2 — and it gains weight: 0.3.23 very
likely cannot load the 2026-Q3 architectures EXP-MR is evaluating.

**Standing precondition — substrate health check:** no task has been routed for ~85 days
(last `runs/cascade.rec` outcome was a LOSE), and Ollama was reinstalled 2026-09-19. Run
one trivial `mesh_solve_canvas.py --topology budget` end-to-end before starting EXP-MR.
(EDGE-1's launch-time table makes this automatic.)

**Shipped (for the record):** #1 PT-1, #2 PT-2, #3 PT-3 CLOSE, #4 gate-helper (#135),
#5 PT-4 HOLD, #6 OBS-1, #7 ts-verify-gate (#115), #8 difficulty-recal (#116),
#9 draft_gate-decompose (#119), #10 ts-shortcut (retired), #11 hook-scope (#133),
#12 obs-legibility (#117), #13 nonblock-hold (#134), #14 verify_func (#130),
**#VR-1 gate registry (#140), #VR-2 shell verifier (#141), #VR-3 JS verifier (#141),
#VR-4 wire call sites (#142), #VR-5 repair-prompt language field (#143)**,
**#SR-1 deterministic replay seed+params (#144)**.

---

## ★ EDGE-1 · `edge` = one-command supervisor launch  (I4 · S2) — HIGH PRIORITY

**Progress:** ✅ **1a** `cascade/health.py` probes + `plan_repairs` (#149). ✅ **1b** edge-cli
supervisor glue (steps 1–6 + step 8 flags): probes skip a dependant whose prereq is down,
`--only` for wait loops, `--json` carries `prereqs`; worker spawn is guarded by a live-process
check (reconnecting worker → `RECOVERED`, never a duplicate). Live-verified on this box:
worker killed → RESTARTED; redis stopped → redis RESTARTED + worker RECOVERED (1 node);
ollama stopped → RESTARTED; warm → all UP, no spawn; `-Check` starts nothing, exits 1 on a
critical DOWN. Cold start (Docker Desktop quit before `edge`) observed 2026-09-19: engine →
redis → worker → session came up in order within ~30 s and routes WIN (launch table not captured).
✅ **1c** step 7 / MD-1: edge-npu/gpu/verify no longer wired by default (`-Servers` = deprecated
opt-in with a warning); the appended prompt is now the pipeline-first CLAUDE.md policy with an
absolute `mesh_solve_canvas.py` command; `edge_summary.py` runs only for opted-in legacy servers.
**Remaining acceptance:** idle VRAM = worker only, checked from a fresh `edge` launch.

**Expectation (user, 2026-09-19):** `edge` is the single command. At startup it checks
that every dependency of the pipeline is up and **restarts any that are down**, then
launches the session.

**Reality after #147:** #147 only added the `edge.cmd` shim → `scripts/edge-cli.ps1`
with default flags. edge-cli has no health-check-and-repair step:

| Dependency | Today | Gap |
|---|---|---|
| venv extras | `uv sync --inexact` | ✅ |
| Docker Desktop | not touched; `-Canvas` only warns if `docker` is missing | never started |
| Redis broker (`edge-cascade-redis`) | `docker compose up -d redis` **only with `-Canvas`** | not checked by default (survives only via `restart: unless-stopped`) |
| Celery worker (npu,gpu,verify) | a new worker window **only with `-Canvas`**, no liveness check | never checked by default; a 2nd `-Canvas` launch spawns a **duplicate** worker |
| Ollama API (:11434) | not touched | never checked or started |
| Dashboard (:8789) | spawned if the port is free, else warn | ✅ roughly right |
| Legacy `edge-npu/gpu/verify` MCP servers | **wired by default**, readiness-probed by `edge_summary.py` | inverted: the *retired* path is the one that's health-checked |

**Why I4 (blocking):** `edge` is the entry point to the whole system, and today it
neither guarantees the Canvas pipeline is up nor steers the agent to it. Evidence: ~85
days with no routed task; the launched session's appended prompt mandates the retired
`edge-npu.route` flow (edge-cli.ps1:386-388); and the default `edge-gpu` server holds the
14b resident (**11.3 / 12.2 GB at idle**), so the Celery worker's first GPU task must load
a second copy into ~0.9 GB free → spill/OOM. The pipeline can't be relied on until the
launcher is fixed. **Why S2:** confined to the launcher plus a small, unit-testable Python
probe helper; every step is idempotent; revert = the previous script. Not S1 because it
starts external processes and waits on them (timeouts, Docker Desktop cold start).

**What — a supervisor pass before launching Claude, in dependency order:**

1. **Docker engine** — `docker info`; if down, start Docker Desktop and wait (bounded,
   ~90 s) for the engine.
2. **Redis** — `docker compose up -d redis`; wait for container health `healthy`.
3. **Ollama** — `GET :11434/api/version`; if down, start `ollama serve` (detached) and
   wait. Needed while it's the experiment backend and the llama_cpp fallback.
4. **Celery worker** — `celery -A cascade.celery_app inspect ping`; start the Slice-5
   worker (`_celery-worker.ps1 -Queues npu,gpu,verify`) **only if no node answers** →
   no duplicates. Wait for its pong.
5. **Dashboard** — existing :8789 logic.
6. **Status table** — one line per dependency: `UP` / `RESTARTED` / `FAILED` (+ how to
   fix). **Critical** deps (Docker, Redis, worker) failing → exit non-zero *before*
   launching Claude unless `-Force`; non-critical (dashboard, Ollama) → warn and continue.
7. **Pipeline-first session** (this is MD-1): stop wiring `edge-npu/gpu/verify` by
   default (keep `-Servers …` as a deprecated opt-in with a warning), and replace the
   appended "MANDATORY … edge-npu.route" prompt with the CLAUDE.md policy — one call to
   `mesh_solve_canvas.py --topology budget`, `capped->tier3` → Tier 3. Playwright stays.
8. **Flags:** `-Canvas` becomes default behaviour (kept as a no-op alias); add `-NoSupervise`
   for fast dev relaunches; `-Check` runs steps 1–6 as probe-only (report, never start).

**Design notes:** put the probe/decision logic in a covered Python module
(`scripts/edge_health.py` or `cascade/health.py`: pure `probe_*()` → status objects +
`plan_repairs(statuses)`), so it's under the 100% gate and testable with fakes; keep
edge-cli.ps1 as thin process-spawning glue. Route the Python helper through the pipeline
per the routing rule; the PowerShell glue is Tier 3.

**Acceptance (all live, on this box):**
- Cold start — Docker Desktop quit, Ollama stopped, no worker → `edge` brings all three
  up, prints `RESTARTED` for each, and launches.
- Warm start — everything up → all `UP`, **no** second worker (`inspect ping` = 1 node).
- Worker killed mid-session → next `edge` restarts exactly one.
- Idle VRAM after launch is the worker's footprint only (no `mcp_servers.gpu` process).
- A trivial `mesh_solve_canvas.py --topology budget` task WINs from the launched session.
- `edge -Check` reports without starting anything; CI green at 100%.

---

## MD arc — deprecate the edge MCP servers completely

**Why (evidence, 2026-09-19):** the per-tier MCP servers (`edge-npu`, `edge-gpu`,
`edge-verify`, `edge-cloud` in `mcp_servers/`) are the *retired* topology — CLAUDE.md
says the Canvas pipeline (`cascade.mesh.solve` via `mesh_solve_canvas.py`) is the single
inference path. But they are still live, and it costs us three ways:

1. **Contradictory agent policy.** `scripts/edge-cli.ps1:386-388` appends a system prompt
   that says *"MANDATORY: … FIRST call edge-npu.route, then … edge-gpu.generate …"* —
   the opposite of CLAUDE.md. Every `edge`-launched session starts with both instructions.
2. **VRAM contention.** `edge-gpu` (`python -m mcp_servers.gpu`) holds the llama_cpp
   14b resident: measured **11.3 / 12.2 GB used at idle** (≈ PT-1's +10.5 GB). A Celery
   GPU worker, SDXL, or an Ollama model on the same card then spills (see
   [FINDINGS-llm-vram-capability.md](FINDINGS-llm-vram-capability.md)).
3. **Dead surface area.** ~950 LOC in `mcp_servers/`, the `mcp` extra, smoke/contract
   tests, edge-cli catalog + status-probe code, and doc references across ~25 files.

**The catch:** `mcp_servers/` is *not* only the servers. Two modules are shared by the
live pipeline and must move first:

| Module | Used by (outside the servers) |
|---|---|
| `mcp_servers/_rec.py` (`make_recorder`, `recorded`, `EXPERIMENT_PREFIX`, `make_experiment_recorder`) | `cascade/tasks.py`, `replay.py`, `scripts/image_server.py`, `scripts/pr_review.py`, `scripts/git_model_bench.py`, `scripts/cli_model_bench.py`, `scripts/warn_prompt_validation_v2.py`, tests |
| `mcp_servers/_funcverify_child.py` (functional-gate sandbox) | `cascade/tasks.py` (`-m mcp_servers._funcverify_child`), `scripts/model_bench.py`, `tests/test_funcverify_child.py` |
| `mcp_servers/_npu_worker_proc.py` | **server-only** (`npu.py`, `smoke_npu_mcp.py`) — delete with the servers |

`cascade/credit_guard.py` was already lifted out (used by `pr_review.py`) — it stays.
The **Playwright** MCP (#147) is not a cascade tier and is **out of scope** — it stays.

### MD-1 · stop wiring the edge servers  (I3 · S1) — ⤴ SUBSUMED by EDGE-1 step 7
Kept for the record; ships as part of EDGE-1.
**What:** In `scripts/edge-cli.ps1`: default `$Servers` → `@()`; replace the appended
"MANDATORY … edge-npu.route" prompt with the pipeline-first policy (one call to
`mesh_solve_canvas.py --topology budget`, cap → Tier 3); keep `-Servers edge-npu,…` as an
explicit opt-in that prints a deprecation warning for one release. Update
`scripts/edge_summary.py` so an empty tier list is a clean READY, not an error.
Local-only follow-up for the user: drop the `mcp__edge-*` allows from the untracked
`.claude/settings.local.json`.
**Why I3:** removes a live contradiction in every agent session's instructions and frees
~10.5 GB VRAM for the real GPU tier. **Why S1:** launcher-only change, no library code;
revert = one flag. **Acceptance:** `edge` launches with no `edge-*` servers; the appended
prompt names the Canvas pipeline only; `nvidia-smi` shows the 14b not resident at idle;
`edge -Check` green.

### MD-2 · relocate the shared modules into `cascade/`  (I2 · S2)
**What:** Move `_rec.py` → `cascade/recorder.py` and `_funcverify_child.py` →
`cascade/funcverify_child.py`; update every importer in the table above; leave thin
re-export shims in `mcp_servers/` for one slice so evidence branches and old scripts
keep running. **Why I2:** pure enabler for MD-3, no user-visible change. **Why S2:**
mechanical move, BUT `mcp_servers/*` is in `coverage.omit` today — landing these in
`cascade/` puts them under the **100% gate**, so any untested branch surfaces as a
failure (per [[coverage-holes-are-deficiencies]] that's a finding to fix, not to omit).
`test_funcverify_child.py` / `test_degen_recorder.py` already exercise most of it.
**Acceptance:** full suite green at 100%; `grep -r "mcp_servers\._rec\|_funcverify_child"`
hits only the shims.

### MD-3 · delete the servers  (I2 · S2) — depends on MD-2
**What:** Remove `mcp_servers/{npu,gpu,verify,cloud,_demo,_npu_worker_proc}.py`, the
shims, `scripts/smoke_npu_mcp.py`, `tests/test_edge_summary_contract.py`, the
`mcp_servers*` entries in `tests/test_smoke_imports.py`, the `mcp` extra in
`pyproject.toml` (+ its `coverage.omit` lines), the CI `--extra mcp`, and edge-cli's
server catalog / status-probe block. **Keep** the `mcp` *package* only if something else
still imports it (Playwright is npx, not Python — check before dropping).
**Why I2:** cleanup; the behaviour change already happened in MD-1. **Why S2:** broad
but deletion-only once MD-2 has cut the dependencies; guarded by CI + the live
`tests/test_canvas_live_behavior.py` probes. **Acceptance:** CI green; `edge` + a live
`budget` route work end-to-end; `mcp_servers/` gone.

### MD-4 · docs sweep  (I2 · S1)
**What:** CLAUDE.md, `.claude/skills/edge-cascade/SKILL.md`, README, RUNBOOK, and the
`pipeline_reminder.py` hook text: remove per-server instructions and mark
`ARCHITECTURE.md` historical. Fold in the known drift: three places still claim *"git/
shell output fails the Python gate → capped→tier3"*, which has been false since VR-2/VR-4
(git routes WIN). Leave `FINDINGS-*`, `PLAN-*` and `evidence/` untouched — they are
history. **Why I2 · S1:** accuracy of the instructions agents act on; text-only.

---

## EXP-MR arc — local model refresh (2026-Q3 open models)

**Why:** the production GPU code model, `qwen2.5-coder:14b`, dates from late 2024. A
2026-09-19 scan of open-weight releases in the prior 60 days found candidates that fit the
12 GB RTX 5070 Ti fully, plus MoE models with only ~3B active parameters that could run
with CPU offload (the box has **63.5 GB RAM**). Details and sources in the session
write-up; published numbers are sparse and mostly vendor/aggregator-reported, so **we
measure, we don't adopt on reputation.** Protocol: the `/experiment` skill (LOCAL
evidence branch, Bayesian MC, `keep_awake`, segregated telemetry, findings leave via a
clean PR citing the evidence sha).

### EXP-MR-1 · 12 GB-resident candidates vs the incumbent  (I3 · S2)
**Arms (all via the Ollama backend — llama-cpp-python 0.3.23 likely can't load the new
architectures; see PT-4):**

| Arm | Model | Q4 size | Notes |
|---|---|---|---|
| control | `qwen2.5-coder:14b` | ~9.0 GB | incumbent; dijkstra 3/3 in FINDINGS-llm-vram-capability |
| T1 | `ornith-1.5:9b` | 6.6 GB | 256K ctx; billed for agentic coding; **no published coding benchmarks** |
| T2 | `granite4.2:8b` | 5.3 GB | IBM, Apache-2.0, dense, FIM; no published coding benchmarks |

**Subjects:** reuse the existing harnesses rather than inventing tasks — (a) the
dijkstra-class functional subjects in `scripts/model_bench.py` (DSL-gated, killed
subprocess), (b) the three parity cases A/B/C from `scripts/parity_batch.py`, (c) NL→git
from `scripts/git_model_bench.py` (incumbent baseline **97%** per
FINDINGS-git-model-selection). Optional (d) one TS subject gated by `ts_verifier`.
**Trials:** ≥ 30 per (arm × subject); record seed + sampling params (SR-1).
**Metrics, in priority order** ([[metric-priorities-quality-cost-over-latency]]):
functional pass rate → Beta posterior, 95% CI, **P(treatment > control)**, paired
per-subject conditionals; cost is $0 for all arms; latency + peak VRAM (at 4k / 16k ctx)
as tiebreakers only.
**Decision gate:** SHIP a candidate as the production GPU model only if P(T > control)
≥ 0.95 on the pooled functional subjects **and** no subject regresses (git ≥ 97%, parity
A/B/C tiers unchanged). The model flip is a separate follow-up PR (`cascade/config.py`
default + FINDINGS doc). Otherwise record REVERT/NULL and keep the 14b.
**Preconditions:** MD-1 landed (or the edge-gpu server stopped) so the card isn't at
11.3 GB idle; substrate health check passed; `ollama pull` both candidates.
**Why I3:** the model choice drives every GPU route's quality — the primary metric.
**Why S2:** measurement-only on a LOCAL evidence branch, $0, no production change until
the separate flip PR. Known unknowns: chat-template / thinking-mode quirks in new models
(Ollama templates handle most; note any manual `/no_think` style switches in the
methodology), and the harnesses importing `mcp_servers._rec` (fine before MD-2; update
paths if MD-2 lands first).

### EXP-MR-2 · MoE partial-offload arm  (I3 · S3) — after EXP-MR-1
**Arm:** `laguna-xs-2.1` (Poolside; 33B total / **3B active**; 20 GB Q4; OpenMDW-1.1;
**SWE-bench Verified 70.9%**, per its Ollama page). Optional second: `nemotron-3.5-lightning`
(NVIDIA; 30B / 3B active; 25 GB Q4). Neither fits 12 GB — run with Ollama's automatic
GPU/CPU split, same subjects + gate as EXP-MR-1, and add wall time per task as a
reported (not decisive) column.
**Why I3:** a quality ceiling far above the 14b class if the offload is tolerable.
**Why S3:** real unknowns — offload throughput on this laptop, whether the new
architectures run correctly in Ollama 0.34.2 on Windows, and the VRAM-cliff behaviour
we measured for dense models may or may not transfer to 3B-active MoE. Stays a
measurement; a production flip would additionally need the GPU backend decision (Ollama
vs llama_cpp → PT-4).

### Follow-up (not yet scored): NPU tier refresh
`granite4.2:3b` (2.2 GB) as a replacement for the 1.5B NPU router/drafter needs an
OpenVINO conversion + NPU-compile spike first, and OpenVINO is 2026.1 vs upstream
2026.4. Score it after that spike tells us whether it compiles for the NPU at all.

---

## ✅ SR-1 · deterministic replay — seed, sampling params, model identity  (I2 · S2) — SHIPPED (#144)

**What:** Make all stochastic generation inputs explicit and logged in the
per-tier `.rec` records so a session can be audited or re-run identically.
Four categories of change per generation path:

1. **Per-call seed** — generate a random `seed` per call, pass it to the model,
   include it in the result dict → captured automatically by the existing
   `@recorded` decorator.
2. **Explicit sampling params** — make temperature and top_p explicit in
   `cascade/config.py` (`CASCADE_GPU_TEMPERATURE`, `CASCADE_GPU_TOP_P`,
   `CASCADE_NPU_TEMPERATURE`) and pass them through each worker instead of
   inheriting backend defaults.
3. **Model identity** — capture once at worker construction, bind into each
   result dict. "Name alone" doesn't pin the weights:

   | Tier | Identity fields to add |
   |------|------------------------|
   | NPU | `model_dir` (CONFIG path), `openvino_genai` version via `importlib.metadata` |
   | GPU Ollama | model **digest** from `/api/tags` response (already fetched by `_available()`), Ollama server version from `/api/version` |
   | GPU llama_cpp | GGUF blob **SHA** (already encoded in the blob path as `sha256-<digest>`), `llama_cpp` version via `importlib.metadata` |

4. **Surface in records** — all of the above appear in the `result` JSON blob
   of every `route`, `draft`, and `generate` record in `edge-npu.rec` /
   `edge-gpu.rec`.

**Design note:** identity fields are cheap to capture at worker init (not
per-call) and then bound into the worker closure, so every result carries
them with zero extra I/O on the hot path.

Files: `cascade/config.py`, `cascade/npu_worker.py`, `cascade/gpu_worker.py`,
`cascade/llama_worker.py`, `cascade/tasks.py`. No changes to `logfmt.py`,
`_rec.py`, or `replay.py` — the grammar and recorder are already correct;
the new fields fall through automatically. Full design in
`C:\Users\danth\.claude\plans\robust-juggling-graham.md`.

**Backend seed support:** OpenVINO GenAI `GenerationConfig.rng_seed`; Ollama
`options.seed`; llama-cpp-python `create_chat_completion(seed=...)`. Cloud
(Anthropic API) excluded — no reproducible seed API.

**Determinism caveat:** seeding makes replay *high-fidelity*, not guaranteed
bit-identical. CUDA non-determinism (cuBLAS, TF32 rounding), driver/version
differences, and NUMA-order variation mean exact bit-reproducibility is not
promised. The goal is the same weights + same seed ≈ same output, good enough
for debugging and experiment auditing.

**Why I2:** observability improvement; doesn't fix a failure mode or unblock
anything. Useful for debugging, experiment reproducibility, and post-hoc audit.
**Why S2:** purely additive — new fields in result dicts, explicit params in
model calls, identity fields bound at worker init. No structural hot-path
changes; config additions follow the established env-var override pattern.

---

## ✅ Verifier Registry arc (VR-1–VR-5)

Design doc: [DESIGN-verifier-registry.md](DESIGN-verifier-registry.md)

**Root cause:** 23/97 routed outcomes capped (24%) wasting 19 min of GPU wall time.
Log analysis (2026-06-01): NPU gate pass rate only 47%; git commands guaranteed LOSE
since the Python AST gate rejects them; `_gate` dispatch lives in `coverage.omit` so
regressions in language dispatch are invisible to the 100% gate.

### ✅ VR-1 · language registry `cascade/gate.py`  (I3 · S2)

**What:** New covered module `cascade/gate.py`: `LanguageVerifier` protocol,
`_REGISTRY`, `_LANG_MAP`, `register()`, `detect_language()`, `gate()`, `gate_any()`.
Self-registers Python and TypeScript at import. Comprehensive unit tests in
`tests/test_gate.py`. **No wiring yet** (topologies_canvas.py still calls its own
`_gate`); this slice is purely additive.

**Why I3:** Foundation for the entire arc; moves dispatch from coverage.omit into the
100% gate. **Why S2:** Additive new module; does not change any production call site.

### VR-2 · shell/git verifier  (I3 · S2)

**What:** `cascade/shell_verifier.py` — `verify_git()` (structural: fence extract +
`git <verb>` regex) and `verify_shell()` (`bash -n` stdin, fail-soft if bash absent).
Registered in `gate.py`. Tests in `tests/test_shell_verifier.py`.

**Why I3:** Directly fixes the git-always-caps problem. Every git route is currently a
guaranteed LOSE; after VR-2+VR-4 it becomes a WIN. **Why S2:** Structural regex + one
`bash -n` subprocess, same fail-soft pattern as `ts_verifier.py`.

### VR-4 · wire call sites  (I3 · S3)  — depends on VR-1+VR-2

**What:** Replace `_gate` body in `cascade/topologies_canvas.py` with a 1-line
delegation to `cascade.gate.gate()`. Replace `tasks.verify_syntax(text)` in
`cascade/wiring.py` with `cascade.gate.gate(text, dsl=None)`.

**Why I3:** Makes VR-1/VR-2/VR-3 real on production routes.
**Why S3:** Touches the hot-path Canvas gate step AND the in-process pipe gate.
Guard with parity check (`scripts/parity_batch.py --backend llama_cpp`, Case B ≤ 37.3s
±20%) and live git route test before merging.

### VR-3 · JS verifier  (I2 · S2)  — can fanout with VR-2

**What:** `cascade/js_verifier.py` — `verify_js()` via
`node --check --input-type=commonjs` stdin (no new npm deps; same node as ts_verifier).
Registered in `gate.py`. Tests in `tests/test_js_verifier.py`.

**Why I2:** New capability, no JS routes in current `cascade.rec`; lower immediate
impact than git. **Why S2:** Same subprocess pattern as ts_verifier, fail-soft on
node unavailable.

### ✅ VR-5 · repair prompt `language` field  (I2 · S2)  — SHIPPED (#143)

**What:** Surface the `language` key from the richer failure dicts in
`cascade/feedback.py:build_repair_prompt()`. The repair instruction can then say
"your **git** command doesn't start with `git <verb>`" rather than "your code has
a syntax error."

**Why I2:** Improves GPU repair-round success rate on non-Python artifacts.
**Why S2:** Additive read of an existing dict key; `CheckFailure` dataclass unchanged.

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

### ✅ #1 · PT-1 — confirm full GPU offload  (I3 · S1) — **DONE 2026-05-31**
**Result: PASS.** VRAM delta +10,526 MB = 123% of 8,571 MB GGUF. All layers on GPU.
Breakdown: 8,571 MB weights + 1,955 MB KV cache + overhead at n_ctx=8192. GPU offload
is NOT the cause of the 3.4× gap. Also surfaced: `n_ctx_train=32768` vs our `n_ctx=8192`;
Ollama likely sizes context dynamically to the prompt (much smaller for typical queries),
so our fixed `n_ctx=8192` over-allocates the KV cache. **PT-2 is the next lever.**

Diagnostic tool: `scripts/pt1_gpu_offload_check.py` (install llama-cpp-python first;
see pyproject.toml `llama-cpp` extra setup notes and the `--no-build` flag workaround).

### ✅ #2 · PT-2 — context + attention config sweep  (I3 · S2) — **DONE 2026-05-31**
**Result: PASS — decision gate met.** 4-config sweep on RTX 5070 Ti Laptop, 2026-05-31:

| Config | Case B | Case C | vs Ollama B |
|--------|--------|--------|-------------|
| 8192, no flash | 42.1s | 23.3s | 31% slower |
| 4096, no flash | 40.9s | 25.7s | 27% slower |
| 4096, flash=True | 38.9s | 25.1s | 21% slower |
| **8192, flash=True** | **37.3s** | **25.6s** | **16% ✓** |

`n_ctx=8192` with `flash_attn=True` is within ±20% on Case B; Case C is faster than
Ollama (skip-repair caps without 2 full repair rounds). `flash_attn=True` is now the
production default in `make_llama_worker`. **Slice 7 unblocked.**

### #3 · PT-3 — KV-cache / system-prompt prefix reuse  (I2 · S3, downgraded from I3)
`_generate` re-prefills the `_SYSTEM` prompt on every call; Ollama's daemon keeps
the session/KV warm. Reuse the prefix KV across calls in the resident worker
(prompt caching). **Reclassified I3→I2·S3 on 2026-05-31**: was I3 because it
blocked Slice 7; the PT-2 gate passed without it (16% vs Ollama, within the bar)
and Slice 7 shipped. Now a pure perf optimization — useful, but gates nothing.
Higher severity: touches the call pattern and risks chat-template / correctness
drift if prefix caching is mishandled — guarded by the parity gate (`parity_batch.py`).

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

## #14 — verify_func refactor  (I2 · S2)

**The gap:** `verify_functional` / the `dsl=` parameter is a useful concept (run the
generated code against assertion test-cases, not just check syntax) but almost never
used in practice because the interface is awkward: callers must hand-craft a raw Python
assertion string, pass it as an opaque `dsl` kwarg, and there's no helper to build or
validate that string before it hits the sandboxed subprocess. As a result `verify_func`
is always 0 in the dashboard — the functional gate exists but is effectively dead code
for real dev tasks.

**Refactor candidates (any subset):**
- A `dsl_from_cases(fn_name, cases)` helper that builds the assertion string from
  structured `(args, expected)` pairs — makes it trivial to attach test cases to a
  `mesh.solve` call without writing raw Python.
- Expose `dsl=` more prominently in `mesh_solve_canvas.py --dsl` CLI flag (it exists
  but is buried; add an example to the help text and the CLAUDE.md routing rule).
- Rename / alias `verify_functional` → `verify_dsl` in `tasks.py` for discoverability
  (the current name implies "functional testing" in the abstract sense, not "run the DSL
  assertions").
- Structured failure output: currently `failures` is a list of raw dicts; a dataclass
  or typed dict would let the repair prompt builder surface better error context.

**Why I2:** the functional gate is genuinely useful for experiment harnesses and
hard-to-gate tasks (parser/interpreter subjects), but it's off the hot path for normal
dev routing — impact is bounded to experiment quality, not session throughput.
**Why S2:** the subprocess sandbox (`_funcverify_child`) is already the isolation
boundary; the refactor touches Python API surface only, not the sandbox itself.

---

## ✅ Slice 7 — SHIPPED 2026-05-31 (PR #127)

Default flipped `ollama → llama_cpp`. PT-2 gate passed (B=37.3s = 16% slower than
Ollama, within ±20% bar). `flash_attn=True` now the production default.
`llama_worker.make_llama_worker` returns an unavailable worker (not a crash) when
the GGUF can't be loaded — required for CI where Ollama isn't installed.
Ollama still works via `CASCADE_GPU_BACKEND=ollama`.

## User-owned (needs the hardware, not agent engineering work)

- **low_latency vs balanced wall-time comparison** — fill the TBD table in
  [FINDINGS-canvas-phase2-low-latency.md](FINDINGS-canvas-phase2-low-latency.md) by
  running `scripts/mesh_solve_canvas.py --topology {balanced,low_latency}` on the
  NPU + RTX + Redis box, then settle the Phase-0 decision gate for low_latency.
