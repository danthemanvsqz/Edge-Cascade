# Design doc — observability data-source lanes (ledger vs liveness)

> Status: **decision doc** (boundary contract for the dashboard's two data
> sources). Scope: lock *which source powers which UI element* before the
> live-activity ring (OBS-1 slice #5) wires Flower into the dashboard.
> Verdict: **two lanes, hard boundary, no cross-feed.** The emitter for the
> live lane does **not** run on the cascade worker.
>
> **Companion:** OBS-1 landed `cascade/flower_activity.py` (the live probe) +
> `runs/cascade.rec` is the existing ledger. This doc governs how the UI
> consumes both without conflating them.

## Context

The dashboard now has access to two data sources, and they look like "two
versions of the truth." They are not — they answer **different questions** over
**different timescales**:

| | `runs/cascade.rec` (LEDGER) | Flower / `snapshot()` (LIVENESS) |
|---|---|---|
| Question | what **happened**? | what's happening **right now**? |
| Nature | durable, append-only system of record | ephemeral, best-effort observation |
| Authoritative for | win/lose, final tier, cost, effectiveness, counts | which node is spinning, live tier occupancy |
| Lifetime | permanent | gone the instant the task ends |
| Latency | lands at task **completion** | live, sub-second |

`.rec` *cannot* show a node spinning (it only lands at completion — the whole
reason Flower was added). Flower *cannot* tell you the outcome (a STARTED task
has no verdict yet). They are complementary, not competing.

The risk is **boundary leakage**: the UI inferring an outcome from liveness, or
inferring liveness from the ledger. This doc forbids that.

## Decisions

### D1 — Two lanes, hard boundary, no cross-feed

- **Liveness flows ONLY from Flower** (`cascade/flower_activity.snapshot`). It
  powers: the spinning-node ring, live tier-occupancy.
- **Outcomes flow ONLY from `runs/cascade.rec`.** It powers: win/lose flash,
  counts, mesh-effectiveness, `$cost`.
- Neither lane derives the other's facts. No "win" inferred from Flower; no
  "currently spinning" inferred from `.rec`. (Precedent: `cascade.rec` vs
  `edge-review.rec` are already kept separate so review spend never conflates
  with cascade spend — same discipline, new axis.)

### D2 — The liveness emitter is OUT of the cascade worker

The cascade worker is `--pool=solo` (one task at a time, resident for warm
NPU/Ollama state). Therefore an emitter implemented as a **Celery task on the
worker's queues would be blocked by the very task it observes**: while
`gpu_solve` grinds ~60s, the solo worker is busy, so a queued `emit_stats` task
cannot run until `gpu_solve` finishes — exactly when the spinning signal is
wanted. **Rejected.**

Flower already observes **out-of-band**: it captures `task-started` events in
its own process (`worker_send_task_events=True`), continuously, occupying no
worker slot. The live truth already exists and already streams; slice #5 only
needs to **deliver** it to the browser.

→ The dashboard server is the **single liveness consumer**: it polls
`snapshot()` on a timer and serves/pushes to the browser. The browser never
talks to Flower directly (Flower stays optional + swappable behind
`flower_activity`).

### D3 — Celery Beat is reserved for a DURABLE live stream only

Beat earns a place **only** if we decide the live signal must be *persisted*
(replay "what was spinning when", or feed `sample_occupancy` to experiments
durably). Even then the sampler is a **dedicated observer** (its own thread/
process or its own queue+worker) writing a **separate** stream
(`runs/cascade-live.rec`), never the hot-path cascade worker (D2). Until that
need is real, the real-time ring uses the D2 poller and persists nothing —
YAGNI.

## The shape (slice #5 builds against this)

```
  WORKER ──events──> FLOWER ──/api/tasks──> snapshot()  ─┐  LIVENESS LANE
   (solo)            (own proc, live aggregator)         ▼
                                              dashboard server
                                          GET /active (poll/SSE) ──> spinning ring
                                                          ▲
  cascade tasks ──append──> runs/cascade.rec ────────────┘  LEDGER LANE
                                          (existing panel: counts / win-lose / $)
```

## Build order (small iterations)

1. **This doc** — lock the boundary. ✅
2. `GET /active` endpoint on the dashboard server → `snapshot()` as JSON
   (Python, gateable/routable).
3. `dashboard/src/flow.ts` spinning-ring on the active node, distinct from the
   post-completion "hot" blip. TypeScript — leans on vitest/tsc/eslint, not the
   Python cascade gate (the edge-verify TS gap).

## Non-goals

- Persisting the live stream (D3 — only if a replay/experiment need appears).
- Replacing `.rec` as the system of record (it stays authoritative for outcomes).
- A push transport decision (poll vs SSE) is deferred to slice #2; either fits
  behind the single-consumer boundary.
