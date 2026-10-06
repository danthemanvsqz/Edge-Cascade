# DESIGN — dashboard playground: run the pipeline directly, watch it flow

**Status:** spec, approved 2026-10-06 (user). Not built. Backlog: **PG arc** in
[BACKLOG.md](BACKLOG.md) (PG-1 … PG-5).
**Audience:** a cold-start agent implementing one PG slice. Read this whole file first;
each slice section lists its files, contract and acceptance.

## 1. Goal

From the dashboard (`dashboard/`, Vinyl + htmx-ws, PM2 `edge-dashboard` on :8789), the
user types a prompt, sends it **straight into the Canvas inference pipeline (no agent, no
Claude session)**, and watches:

1. the run move through the flow graph (`dashboard/src/flow.ts`) node by node;
2. a per-run **event log**: each chain step, its state, its duration, and the data that
   went in and came out;
3. the **worker's log lines** for that run.

It is a test bench for the pipeline: try a prompt, a DSL, a topology, and see exactly
where it wins, repairs, or caps.

## 2. Decisions (user, 2026-10-06)

| # | Decision | Consequence |
|---|---|---|
| D1 | Playground runs **are recorded** in `runs/cascade.rec`, tagged `source=playground` | DX-1, NH-5 and every `/experiment` analysis **exclude `source=playground` by default** (opt-in flag to include). Agent-routed runs get `source=agent`; records without the field are historical = `agent`. |
| D2 | **This laptop only**: no auth | `/api/run` (and, simplest, the whole server) binds `127.0.0.1`. Today `server.listen(port)` binds **all interfaces**; PG-2 fixes that. |
| D3 | **Per-run settings deferred** to PG-5 | v1 shows the current config read-only (temperature, backend, flags from env). |

## 3. What exists (verified 2026-10-06)

- **Dispatch:** `scripts/mesh_solve_canvas.py` → `cascade.canvas_client.solve_budget_canvas`
  → `budget_signature(query, dsl).apply_async().get(timeout=600)` → `_to_outcome(env)` →
  `_record_outcome()` appends to `runs/cascade.rec`. The dispatch and the blocking wait are
  fused in one call, so the caller never sees the chain's ids before it finishes.
- **Live node lighting:** `scripts/cascade_live_receiver.py` runs a Celery
  `app.events.Receiver` (it consumes **every** task event; `worker_send_task_events=True`,
  `task_track_started=True` in `cascade/celery_app.py`). It publishes only node
  active/idle deltas to Redis `cascade.live.nodes` (+ seed key `cascade.live.active`), with
  **no run identity**. `dashboard/src/lib/liveSource.ts` subscribes.
- **Topology:** Beat pushes the graph on `cascade.live.topology`; `flow.ts` renders it.
- **Dashboard server:** `dashboard/src/server.ts`. Plain `node:http` routes (`GET /`,
  `POST /api/reset`, `GET /style.css`); state lives in `store.ts`; pushes go over the Vinyl
  WS hub.
- **Node has no Celery client.** Reimplementing Celery's message protocol in TypeScript
  would fork the canonical dispatch path. **Rule: the dashboard never builds Celery
  messages; it spawns the Python client.**

## 4. Architecture

```
browser ──POST /api/run {query, dsl, topology}──► dashboard (Node, 127.0.0.1:8789)
   ▲                                                │ spawn (argv array, no shell; prompt on stdin)
   │ WS pushes (Vinyl hub)                          ▼
   │                         uv run python scripts/mesh_solve_canvas.py --json --source playground
   │                                                │ stdout JSONL: dispatched{root_id} … outcome{…}
   │                                                ▼
   │                         Celery chain on the worker (npu, gpu, verify queues; never cloud)
   │                                                │ task events            │ log records
   │                                                ▼                        ▼
   │                         cascade_live_receiver ──► Redis pub/sub ◄── RedisLogHandler (worker)
   │                           cascade.live.nodes (existing)      cascade.live.logs (PG-4)
   │                           cascade.live.events (PG-3)
   └──────────── liveSource.ts subscribes, filters by the current run's root_id ◄┘
```

**Run identity = the chain's `root_id`.** Every task in a chain (including tasks swapped
in by `self.replace()`) carries the same `root_id` in its Celery events, so it is the one
key that ties dispatch, events, logs and the outcome together.

## 5. Invariants

1. **No cloud spend.** The playground only spawns `mesh_solve_canvas.py`; the worker never
   consumes the `cloud` queue (Slice 5 launcher refuses it). A capped run ends at
   `capped→tier3`, the same as today. Test: the spawned argv never contains a cloud opt-in.
2. **One run at a time.** One GPU. A second `POST /api/run` while one is in flight returns
   `409 {running: root_id}`.
3. **Only "Re-run" re-runs.** Page load or WS reconnect never dispatches.
4. **Prompt never on the command line.** stdin only. This avoids the Windows ~32 K argv
   limit, quoting, and injection. argv is a fixed array; no `shell: true`.
5. **Best-effort telemetry.** A dead receiver or log handler degrades the view (event log
   empty, banner shown); it never fails or blocks the run.
6. **Cancel is honest.** Cancel revokes the chain's not-yet-started tasks. A step already
   running on the solo-pool worker (e.g. a 60 s GPU generate) cannot be killed; the UI says
   "cancel requested, waiting for <step> to finish".

## 6. Slices

### PG-1 · `mesh_solve_canvas.py --json`, stdin prompt, `source` tag  (I2 · S1)
**Files:** `cascade/canvas_client.py`, `scripts/mesh_solve_canvas.py`, tests.
**What:**
- Split dispatch from wait: `dispatch_budget(query, dsl) -> AsyncResult` and
  `wait_outcome(result, query, t0, source) -> Outcome`; `solve_budget_canvas` becomes the
  two composed, so behavior is unchanged. Same split for `low_latency`. `root_id` = the
  root of the returned result's parent chain (walk `.parent` to the first task; assert
  it equals the `root_id` Celery stamps on events in a live check).
- `--json`: print one JSON object per line: `{"event":"dispatched","root_id":…,
  "topology":…,"ts":…}` immediately after `apply_async`, then
  `{"event":"outcome", …Outcome fields…, "wall_s":…}` or
  `{"event":"error","message":…}`. No other stdout in this mode.
- `--query-file -` reads the prompt from stdin (positional `query` becomes optional when
  given).
- `--source {agent,playground}` (default `agent`) → `_record_outcome` writes a `source`
  field to `runs/cascade.rec`.
- Readers that must exclude playground runs by default: `replay.py` (repo root), the dashboard's outcome counters in `dashboard/src/store.ts` (show playground runs, but labelled and separable), and any ledger
  analysis named in DX-1 / NH-5. Add the default-exclude filter where a reader exists
  today; note the rule in their docstrings.
**Acceptance:** unit tests at the Celery seam (fake AsyncResult) cover the JSONL shape,
stdin, and the `source` field; the human-readable output is byte-identical without
`--json`; one live run prints `dispatched` before the GPU step starts; CI green at 100%.

### PG-2 · run panel + `POST /api/run`  (I3 · S2) — after PG-1
**Files:** `dashboard/src/server.ts`, `dashboard/src/app.ts`, new `dashboard/src/run.ts`
(spawn + JSONL parse + one-at-a-time state), `dashboard/src/panels.ts` (panel),
`store.ts` (run state + last-10 history), tests in `dashboard/test/`.
**What:**
- Bind to `127.0.0.1` (D2), with `HOST` env override documented in `server.ts`'s env-knob
  header.
- Panel: prompt textarea, optional DSL textarea, topology select (`budget`,
  `low_latency`), Run / Cancel buttons, read-only current config (D3). htmx `hx-post`
  → `202 {run_id: root_id}` / `409` / `400` (empty prompt).
- `run.ts`: `spawn("uv", ["run","python","scripts/mesh_solve_canvas.py","--json",
  "--source","playground","--topology",t,"--query-file","-", …dsl])` with `cwd` = repo
  root, the prompt written to stdin, stdout parsed line by line (the JSON parser is pure
  and tested; spawn is injectable so tests use a fake child). Child exit without an
  `outcome` line → run state `error` with stderr tail.
- Renders: status (dispatched → running → resolved / capped / error), final tier, repair
  rounds, wall time, answer (code block), trace. History list of the last 10 runs with
  Re-run.
**Acceptance:** vitest covers 202/409/400, JSONL parsing incl. a truncated line, the
child-error path, and that argv contains no prompt text; live: a prompt runs from the
browser and its outcome appears; the existing node lighting animates during it.

### PG-3 · per-run event log + step inspector + path highlight  (I3 · S2) — after PG-2
**Files:** `cascade/live_receiver.py` (pure projection), `scripts/cascade_live_receiver.py`
(publish), `dashboard/src/lib/liveSource.ts`, `store.ts`, `panels.ts`, `flow.ts`, tests.
**What:**
- Pure `task_event_frame(event) -> dict | None` in `live_receiver.py`: from Celery events
  `task-received / task-started / task-succeeded / task-failed / task-retried`, emit
  `{root_id, task_id, name, node, state, ts, runtime, args, result, exception}`, with
  `args` and `result` truncated to 1 KB each (Celery's `resultrepr_maxsize` /
  `argsrepr` already bound these; truncate again so the frame bound is ours). The receiver
  publishes frames on **`cascade.live.events`**; the existing node deltas are untouched.
- Dashboard keeps frames whose `root_id` = the active run (and the history runs, capped at
  500 frames per run). Event-log panel: a timeline of `node · state · duration`. Clicking a
  row expands the input/output (the envelope fields that matter: `query`, `difficulty`,
  `category`, draft text, gate result + failures, GPU candidate, final tier).
- `flow.ts`: the nodes and edges this run visited get a persistent highlight until the
  next run, on top of the existing global lighting.
**Acceptance:** pytest covers `task_event_frame` for each event type, truncation and a
missing `root_id` (returns None); vitest covers filtering by `root_id` and the cap; live: a
capped run's timeline shows route → draft → verify → gpu_solve (with repair rounds) →
done, and clicking gpu_solve shows the candidate.

### PG-4 · worker log shipping + log pane  (I2 · S2) — after PG-3
**Files:** new `cascade/live_logs.py`, `cascade/celery_app.py` (install handler on
`worker_process_init` / `setup_logging` signal), dashboard `liveSource.ts`, `panels.ts`.
**What:** `RedisLogHandler(logging.Handler)` publishes
`{ts, level, logger, message, task_id, root_id}` to **`cascade.live.logs`**. `task_id` /
`root_id` come from `celery.current_task.request` when inside a task, else null. Rate
limit: drop and count above 200 lines/s; one Redis client per process; `emit()` swallows
every error (invariant 5). Dashboard log pane: lines for the active run, level filter,
autoscroll with pause-on-scroll-up.
**Acceptance:** pytest with a fake redis covers the frame, the no-task context, the rate
limit, and that a publish error doesn't raise; live: a run's GPU step log lines appear in
the pane while the step is running.

### PG-5 · per-run settings  (not yet scored) — deferred (D3)
Temperature, `CASCADE_RAG_TARGET`, NH-3 strategy, NH-2 extraction per run. Prerequisite:
the chain reads these from the envelope (`env["settings"]`) with env-var defaults,
instead of from `CONFIG` at import. That change touches every worker, so it is scored
after NH-3 and KB-2 exist.

## 7. Out of scope (v1)

- Multi-run concurrency or a queue (invariant 2).
- `budget_fanout` from the panel (needs a multi-prompt UI; the CLI covers it).
- Persisting history across dashboard restarts (`cascade.rec` already persists outcomes).
- Remote access / auth (D2).
- Killing an in-flight GPU step (solo pool; invariant 6).

## 8. Open risks

- **`root_id` through `self.replace()`.** Celery docs say replaced tasks inherit the
  chain's root; PG-1's live check verifies it on the budget chain before PG-3 relies on it.
- **Event payload size.** Celery `argsrepr` for the envelope can be large (draft text,
  candidates). The 1 KB truncation keeps Redis frames small; the inspector shows
  truncated values with a "truncated" marker. Full values stay in `cascade.rec`.
- **Receiver lag.** Celery events can arrive slightly out of order; the dashboard orders a
  run's timeline by event `ts`, not arrival.
