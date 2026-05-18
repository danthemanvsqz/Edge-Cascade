# PLAN — Replayable logs + live metrics dashboard

> **For a fresh session picking this up cold.** This is an approved
> implementation plan. The decisions below are *already made by the user* —
> do not re-litigate them; build it. Read the "Grounding" section first so you
> don't re-derive what already exists.

## Goal

Two features for `edge-cascade` (repo `C:\Users\danth\src\edge-cascade`):

1. **Replayable logs** — reconstruct the true ordered timeline of what the
   cascade did, from the structured logs, independent of chat narration.
2. **Live metrics dashboard** — a self-refreshing terminal view showing the
   system is running, MCPs are producing results, plus failures, escalations,
   and (zero) spend.

Context: this project runs as a 4-tier mesh where Claude Code itself is Tier 3
driving local MCP servers (`edge-npu`, `edge-gpu`, `edge-verify`,
`edge-cloud`). See memory `edge-cascade-cli-as-tier3` and `RUNBOOK.md`.

## Decisions already locked (do not revisit)

1. **Add `ts` + `run_id` to the live recorder** (the only live-code change;
   user approved the trade-off vs. strictly log-only).
2. **Replay = timeline reconstruction viewer** (read-only; NOT
   deterministic re-run/regression replay — that was explicitly deferred).
3. **Metrics = a live, auto-refreshing terminal dashboard** (not just an
   on-demand report). Terminal is fine; it should be "somewhat live".
4. Defaults (flag-configurable): dashboard refresh **2s**, episode idle-gap
   **30s**. **No new dependency** — stdlib ANSI clear+redraw; structure the
   render layer so `rich.Live` is an easy later swap.

## Grounding — what already exists (don't rebuild)

- **`cascade/logfmt.py::parse_stream(text) -> list[dict]`** — deterministic,
  total parser for the `.rec` length-framed grammar. THE foundation; both
  features are read-only consumers of it. Do not modify logfmt.py (it is in
  the scoped-100% coverage set).
- **`mcp_servers/_rec.py`** — `make_recorder(server)` returns an
  `emit(tool, fields)` closure; the `recorded` decorator wraps every MCP tool.
  Single chokepoint: every tool call on every server appends one record with
  `server, tool, args(JSON), ok, result(JSON)|error, latency_ms` to
  `runs/<server>.rec`. `_seq` is per-process `itertools.count()` — **resets to
  0 on every server restart** (this is why we need `ts`/`run_id`).
- **`cascade/orchestrator.py::write_record`** — the cli.py path writes
  `runs/cascade.rec` with `query, answer, final_tier, total_latency_s, trace`
  (explicit hop list). The agentic flow does NOT write cascade.rec — only the
  per-server streams exist there.
- **`validate_log.py`** — precedent/shape for a root-level log-consuming CLI:
  `argparse`, `from cascade.logfmt import parse_stream`, reads `runs/*.rec`.
  New tools must match this convention (root-level `*.py`, run via
  `uv run python <tool>.py`).
- Streams live in `runs/` (gitignored). Records have **no timestamp/session
  id today** — that is the gap Phase 1 closes.

## Phase 1 — Recorder change (only live-code touch; ~6 lines; behavior-neutral)

- `mcp_servers/_rec.py`: in `make_recorder`, create a process-stable
  `run_id` once (e.g. `uuid.uuid4().hex[:12]`). In `emit()`, add to every
  record: `"ts": f"{time.time():.3f}"` and `"run_id": run_id` (before
  `**fields`). One place → all 4 servers.
- `cascade/orchestrator.py::write_record`: add the same two fields to the
  `cascade.rec` record dict.
- Append-only, backward-compatible: `parse_stream` already tolerates
  missing/extra keys. Replay/metrics MUST handle old records lacking
  `ts`/`run_id` (fall back to file order + `run_id="unknown"`).
- **CI-safe**: `_rec.py` (mcp_servers) and `orchestrator` are outside the
  scoped-100% gate (`orchestrator` is in `[tool.coverage.run] omit`).
- After the change: do one real run (e.g. `uv run python
  scripts/probe_repair_path.py`) so fresh `ts`/`run_id` records exist to
  build against.

## Phase 2 — `replay.py` (root-level CLI, read-only)

- Mirror `validate_log.py` shape. `from cascade.logfmt import parse_stream`.
- Read all `runs/*.rec` (4 per-server + `cascade.rec`), tag each record with
  its source server, merge, sort by `ts` (records without `ts` → keep
  file-append order, sort stably after timed ones or treat as a legacy
  episode).
- Group into **episodes** (see Sessionization). Render each episode as an
  ordered human trace: `prompt → route(difficulty/category) →
  draft(device,latency) → verify_syntax/functional(pass/fail,reason) →
  repair_prompt → gpu.generate(tok/s) → … → outcome/final_tier`.
- Flags: `--last N`, `--run <run_id>`, `--server <name>`, `--failures-only`,
  `--json`, `--gap <seconds>` (episode split, default 30).
- Purpose: the auditable, prose-independent ground truth of what happened.

## Phase 3 — `dashboard.py` (live terminal dashboard, read-only)

- Loop: re-parse `runs/*.rec` every `--interval` (default 2s), clear screen
  (ANSI `\x1b[2J\x1b[H`; works in Windows Terminal) + redraw. Ctrl-C exits.
  `--once` = single snapshot. `--json` = machine snapshot. Stdlib only.
- Keep a pure `compute_metrics(records) -> dict` function (unit-testable)
  separate from the render loop, and an isolated `render(metrics) -> str`
  (so `rich.Live` can replace render later without touching compute).
- Panels (map 1:1 to the user's asks):
  - **MCP liveness**: per server — reachable?/age-of-last-call, total calls,
    ok vs err, success %, latency p50/p95/max.
  - **Producing results**: non-empty-result rate; NPU draft truncation rate
    (verify reason "no fenced code block"); route() difficulty distribution.
  - **Failures**: tool errors by type (`ok:false` + `error`); gate failures
    split **syntax vs functional**; GPU-unavailable events.
  - **Escalations**: NPU→GPU hop count; repair-round histogram; cap-hits
    (2 failed rounds → Tier-3); final-tier distribution (cascade.rec where
    present; inferred from per-server sequence otherwise).
  - **Spend (headline invariant)**: `edge-cloud` call count + Σ
    `est_cost_usd` from results — must show **$0.00 / 0 calls**; render RED
    if nonzero.

## Cross-cutting — Sessionization heuristic

`run_id` is per *server process* (the NPU server stays resident across many
tasks), so it ≠ "one task". Define an **episode** = events on the merged,
ts-sorted stream split on an idle gap > `--gap` (default 30s). `cascade.rec`
records give exact boundaries for the cli.py path; for the agentic path the
gap heuristic is "good enough" for viewing/counting. Document it as a
heuristic, not ground truth.

## Tests & docs

- Do NOT modify `cascade/logfmt.py` (keeps it at scoped 100%).
- New modules are entrypoints like `validate_log.py` → covered by existing
  `omit` policy. Add focused unit tests for the PURE parts only —
  episode-splitting and `compute_metrics` — using synthetic in-memory `.rec`
  strings (build via `cascade.logfmt.dump_record`). No hardware; CI-safe.
  Follow project test style (pytest-mock if mocking; functional Pythonic).
- Update `README.md` (new tools under the agentic-flow section) and
  `RUNBOOK.md` (dashboard = at-a-glance health during a launched session).
- Do not wire these into the pre-push hook (not requested).

## Sequencing

1. Phase 1 recorder change → one real probe run to emit new-format records.
2. `replay.py` (validates `ts`/`run_id` end-to-end).
3. `dashboard.py` (reuses Phase 2's parse/merge layer).
4. Tests + README/RUNBOOK updates.

## How to start (cold session)

1. Read this file, `RUNBOOK.md`, and skim `cascade/logfmt.py`,
   `mcp_servers/_rec.py`, `cascade/orchestrator.py` (`write_record`),
   `validate_log.py`.
2. Confirm branch (work was last on `feat/cli-tier3-launcher-and-gate`).
3. Execute Phase 1 → 4 in order. Verify each against `runs/*.rec` (the
   recorder is the source of truth; chat narration is not — see RUNBOOK
   honesty rule).
