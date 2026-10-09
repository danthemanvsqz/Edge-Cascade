# Testing strategy

edge-cascade is tested in **five layers**, from fastest and most isolated to slowest and most real.
Each module is tested in the layer where a test gives real assurance. Hardware, broker and model
behavior is tested against the real thing, not mocks: a mock of an NPU or a Celery broker only proves
the test agrees with the mock ("mock theater").

**Supported environment: Dan's laptop only** (Intel Core Ultra NPU + NVIDIA RTX, Ollama, local Redis,
Windows). That is the only machine edge-cascade runs on, so it is the only machine the full test
pyramid targets. CI runs the hardware-free layer only.

| # | Layer | What it proves | How to run | Where it runs |
|---|---|---|---|---|
| 1 | **Gated unit core** | Pure, safety-critical logic is fully exercised | `uv run pytest` | CI + pre-commit; **`fail_under = 100`** |
| 2 | **Ungated unit tests** | Logic inside hardware/broker modules, via eager Celery and fakes where a fake is honest | `uv run pytest` (same run) | CI + pre-commit; not counted in the gate |
| 3 | **Integration** | Canvas topologies run end to end on an embedded Celery worker + in-memory broker | `uv run pytest -m integration --no-cov tests/test_canvas_budget_integration.py` | **Local only** (opt-in) |
| 4 | **Live** | Real worker + Redis + NPU/GPU/verify hardware, no mocks, no embedded worker | `uv run pytest -m live --no-cov` | **Local only**; auto-skips when no live worker is up |
| 5 | **Pre-push end-to-end gates** | The real pipeline works and its invariants hold | runs on `git push` (pre-push stage) | **Local only**; skip (exit 0) without hardware |

## Layer 1 — gated unit core (100%)

`[tool.coverage.run]` in `pyproject.toml` scopes the 100% gate to everything under `cascade/` and
`scripts/` that is **not** in `omit`. As of 2026-10-09 that is 25 modules:

- **Escalation gate + verifiers:** `verifier`, `gate`, `js_verifier`, `ts_verifier`, `shell_verifier`, `degeneration`, `degen_recorder`
- **Repair protocol + routing logic:** `feedback`, `mesh`, `topologies`, `low_latency_pick`, `model_swap`, `wiring`
- **Spend safety:** `cloud_worker`, `credit_guard`, `config`, `reviewer`, `review_ledger`
- **Observability + health:** `logfmt`, `live_receiver`, `flower_activity`, `health`
- **Scripts:** `edge_summary`, `calibrate_degeneration_thresholds`, `sdxl`

Rule: if a module in this layer drops below 100%, CI fails. Untested paths here are defects, and so
is any `omit` entry that hides logic instead of hardware glue.

## Layer 2 — ungated unit tests

Modules in `omit` still have unit tests where a test is honest (pure helpers, eager-mode Celery):
`tasks` and `celery_app` (9 test files), `canvas_client` (5), `topologies_canvas` (4),
`gpu_worker` / `llama_worker` (3), `npu_worker` / `orchestrator` / `lookahead` / `canvas_spike` (2).
The image path is tested at the spec boundary (`test_image_spec.py`, `test_sdxl.py`). These run in
the same `uv run pytest` invocation but don't count toward the gate, because the rest of each module
needs real hardware or a running broker.

## Layer 3 — integration

`tests/test_canvas_budget_integration.py` boots an embedded Celery worker
(`celery.contrib.testing.worker.start_worker`) against an in-memory broker and runs the Canvas
topologies end to end. **Opt-in and local only**: the embedded worker's shutdown hangs at teardown on
Windows, which would stall every default run.

## Layer 4 — live

`tests/test_canvas_live_behavior.py` drives the **real** running Celery worker, Redis broker and
NPU/GPU/verify hardware. Opt-in via `-m live`; auto-skips when no live worker is up.

## Layer 5 — pre-push end-to-end gates

Wired in `.pre-commit-config.yaml` at the pre-push stage. Both **SKIP cleanly (exit 0) without the
hardware**, so a hardware-less push is never blocked; on the laptop a failure blocks the push.

- **`e2e-local`** (`scripts/e2e_local.sh` → `e2e_local.py`): happy path route → draft → verify over
  the real MCP servers. Cloud disabled (zero spend). Self-bounded at 90 s, hard `timeout 300`.
- **`repair-path-gate`** (`scripts/probe_repair_path.sh`): the real NPU → verify → GPU → re-gate →
  2-round cap loop. **Asserts the invariants:** the deterministic gate rejects a known-bad draft, and
  **no paid (edge-cloud) call ever happens**. The stochastic GPU outcome (repaired vs capped → Tier 3)
  is not a failure.

## Exercised by normal use (not a test layer)

- **`topology_graph`** — pure data (frozen dataclasses). Published by the `cascade.push_topology` Beat
  task on worker startup and every 30 s, and rendered by the dashboard, so every `edge` launch with
  the dashboard up exercises it. No dedicated test.
- **`image_worker` / `scripts/image_server.py`** — **experimental, unsupported.** Image generation
  exists only because Claude doesn't generate images; it hasn't been used in a while. Only its spec
  boundary is unit-tested (layer 2). Treat failures there as expected until it is revived.

## What CI does and doesn't run

CI (`.github/workflows/ci.yml`, Windows runner) runs **layers 1–2 only** (`uv run pytest`, which
excludes `integration` and `live` markers) plus the bandit security gate. Layers 3–5 need the laptop's
hardware or hang on Windows, and the laptop is the only supported environment, so they run locally.
A green CI badge means: the 100% core gate held, the ungated unit tests passed, and bandit found no
egregious issues. It does **not** mean the hardware path was exercised; the pre-push gates cover that.

## Adding code

- New pure logic → put it in a layer-1 module (or extract it into one) and cover it 100%.
- New hardware/broker glue → add it to `omit` **only** if the logic has been extracted, and cover the
  behavior in layer 3, 4 or 5. Update this doc in the same PR.
