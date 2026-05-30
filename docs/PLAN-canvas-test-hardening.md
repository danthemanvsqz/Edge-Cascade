# Plan — harden the Canvas regression net BEFORE the draft_gate refactor (#9)

> Status: **plan / handoff**. Author: prior session. Executor: fresh Opus
> session ("the testing commit").
> **Scope is TESTS ONLY. Do NOT touch production code. Do NOT start the #9
> draft_gate decompose.** This plan builds the behavioral safety net so #9 can
> be done confidently afterward, as a separate piece of work.

## Why this exists

#9 (split the overloaded `mesh.balanced._draft_gate` into `verify` / `resolve`
/ escalate nodes) is an **S3 hot-path Canvas-chain refactor**. It must preserve
two load-bearing invariants:
- **cap invariant** — `tests/test_mesh.py::test_cap_is_never_exceeded` (the GPU
  is called exactly `repair_cap` times, never more).
- **pipe-parity contract** — a Canvas run's `mesh.Outcome` (answer / final_tier
  / trace) matches the in-process `mesh.solve` path
  (`docs/FINDINGS-canvas-phase1.md`).

Refactoring the hot path against only **brittle, mock-heavy unit tests** is the
risk. Establish a real behavioral net first.

## Current regression protection (analysis — verify before trusting)

| Layer | File | Mocks at | Covers resolve/escalate? | Counts toward 100% gate? |
|---|---|---|---|---|
| Unit (eager) | `tests/test_canvas_balanced.py` | **function boundary** (route/draft/verify/generate) — the heavy mock chains | yes (parity cases) | **NO** — `cascade/topologies_canvas.py` is in `[tool.coverage.run] omit`, so these mocks earn **zero** coverage |
| Integration | `tests/test_canvas_balanced_integration.py` | **model boundary only** (llama/ollama); real embedded worker + `memory://` broker + real chain dispatch + `.get()` | **YES** — `resolves @ npu`, `escalates @ gpu`, `holds cap` | no (omitted substrate) |
| E2E | `scripts/e2e_local.py` (+ `e2e_local.sh`, pre-push) | **none** (real Intel hw), skips w/o hw | **NO** — drives the *retired MCP path* (`edge-npu/gpu/verify` stdio servers), **not** the Canvas chain |

**Two gaps this plan closes:**
1. **No outside-in E2E of the Canvas chain.** The only no-mock hardware test
   exercises the old MCP topology, not `cascade.mesh.solve` / the Canvas
   signature that #9 changes.
2. **The unit layer is brittleness with no payoff** — its mock-soup doesn't
   count coverage (omitted substrate), while the integration tests prove the
   same paths more robustly with model-boundary mocks only.

## Work items (tests only)

### WI-1 — Outside-in Canvas E2E (`scripts/e2e_canvas.py` + pre-push hook)
- Drive the **real Canvas pipeline** end to end: real Celery worker on
  `npu,gpu,verify` + Redis broker (or the in-process `cli.py` pipe path if the
  broker is unavailable) + **real NPU/GPU**. **No mocks.**
- Assert the two load-bearing behaviors the refactor must preserve:
  - (a) an **easy** task resolves at **npu** (`final_tier == "npu"`, draft gate
    PASS) — the resolve path.
  - (b) a **hard** task **escalates to gpu** (or caps) — the escalate path.
  - (c) the run **self-logs** a record to `runs/cascade.rec` (win/lose logger).
- **Skip cleanly (exit 0) without hardware / extras** — mirror `e2e_local.py`'s
  skip contract so CI and hw-less boxes are never blocked.
- **NOT coverage-tracked** — it's a `scripts/` entrypoint; add it to
  `[tool.coverage.run] omit`. Wire as a `pre-push` hook in
  `.pre-commit-config.yaml` next to `e2e-local`.

### WI-2 — Pin current behavior as the isolated-integration parity guard
- In `tests/test_canvas_balanced_integration.py` (the GOOD layer — model-
  boundary mocks, real chain), add **characterization tests** that snapshot the
  EXACT current Outcome for the paths #9 touches, so the refactor must preserve
  them:
  - npu-resolve: `final_tier == "npu"` AND the trace contains `"npu gate PASS"`.
  - gpu-escalate: NPU gate FAIL → `final_tier == "gpu"`; trace contains
    `"npu gate FAIL"`.
  - cap-hold: all-fail → `capped->tier3`, GPU called exactly `cap` times.
- These are the **byte-level trace/Outcome pins** the #9 refactor will be held
  to (the parity contract made executable at the integration layer).
- Keep mocking at the **model boundary only**; do not add function-boundary
  mock chains.

### WI-3 — (judgment call, ask before doing) thin the no-payoff unit mocks
- `tests/test_canvas_balanced.py`'s function-boundary mock chains cost
  maintenance for zero coverage (omitted substrate) and duplicate what WI-2
  proves more robustly. **Candidate for pruning** once WI-1/WI-2 are green — but
  confirm with the human first; some eager cases may pin logic worth keeping.

## Working mode (expectations set by the human)
- **Iterative.** Expect cycles: write a test → run against current code →
  observe the real Outcome/trace → tighten. Use the integration worker fixture
  (`conftest.py::celery_integration_worker`) for fast, broker-real runs.
- **Red is expected pre-commit.** It's fine to have many failing tests while
  discovering the seams (the real trace strings, the exact final_tier, the cap
  count). Don't force green prematurely by weakening assertions.
- **May not end in a PR.** This can be a series of exploratory commits on a
  dedicated branch; the human decides when/whether to PR.

## Definition of done (for THIS scope)
- WI-1 E2E exists, runs the **real Canvas chain**, and is **green on the current
  (pre-refactor) code** (or skips w/o hw); wired pre-push; omitted from coverage.
- WI-2 characterization tests are **green on current code** and pin the
  npu-resolve / gpu-escalate / cap traces + final_tier.
- The 100% unit-coverage gate still passes (these additions don't move it).
- **No production code changed.** #9 is a clean follow-on against this net.

## Constraints / contracts (do not violate)
- Tests/E2E must **not** count toward the 100% gate (omit substrate + scripts).
- Preserve the **skip-without-hardware** CI-safe contract.
- Spend invariant: **no worker on the `cloud` queue** (the embedded integration
  worker subscribes only `npu,gpu,verify`).
- Touch **only** test files, `scripts/e2e_canvas.py`, `.pre-commit-config.yaml`,
  and the coverage `omit` list. Nothing in `cascade/` production logic.

## Branch
`test/harden-canvas-pre-9` off `main` (NOT the `feat/draft-gate-decompose`
branch — keep the net independent of the refactor).

## Key references
- Analysis above (this session). Chain: `cascade/topologies_canvas.py`
  (`balanced_signature`, `_balanced_draft_gate` at the split point).
- Invariants: `test_mesh.py::test_cap_is_never_exceeded`,
  `docs/FINDINGS-canvas-phase1.md` (parity).
- Skip-contract template: `scripts/e2e_local.py` / `scripts/e2e_local.sh`.
- Integration fixtures: `tests/conftest.py`.
