# Design doc — observability data-source lanes (ledger vs liveness)

> Status: **decision doc** (boundary contract for the dashboard's two data
> sources). Scope: lock *which source powers which UI element* before the
> live-activity ring (OBS-1 slice #5) wires the live task-event stream into the
> dashboard.
> Verdict: **two lanes, hard boundary, no cross-feed.** The live lane is
> **event-driven push, no UI polling** (Vinyl WS): a Celery **event receiver**
> (`app.events.Receiver` — the same events subsystem Flower runs on, callback per
> task transition) pushes node-state via Redis pub/sub to the dashboard. A
> receiver is a *consumer*, not a task, so it sidesteps the solo-worker trap by
> construction and catches every transition (incl. sub-100ms NPU steps a poll
> misses). Beat was considered and rejected for this signal — it samples an event
> stream and drops fast transitions; reserved for genuinely periodic work.
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

- **Liveness flows ONLY from the live worker task-event stream** (the event
  receiver for the dashboard; `flower_activity.snapshot` for poll-tolerant CLI/
  experiments). It powers: the spinning-node ring, live tier-occupancy.
- **Outcomes flow ONLY from `runs/cascade.rec`.** It powers: win/lose flash,
  counts, mesh-effectiveness, `$cost`.
- Neither lane derives the other's facts. No "win" inferred from Flower; no
  "currently spinning" inferred from `.rec`. (Precedent: `cascade.rec` vs
  `edge-review.rec` are already kept separate so review spend never conflates
  with cascade spend — same discipline, new axis.)

### D2 — Liveness is EVENT-DRIVEN PUSH; the UI never polls

The Vinyl dashboard is already a websocket-push app (`streamShell` paint +
`app.vws` live-region fabric); the browser only *receives*. **No polling in the
UI** — that's the whole reason Vinyl was chosen. So the live lane must *push* to
the browser, not be pulled.

The signal is event-driven by nature — discrete `task-started` / `task-succeeded`
transitions at irregular times — so it's driven by a Celery **event receiver**,
not a poll. A small `app.events.Receiver` process registers callbacks on
`worker-heartbeat` / `task-started` / `task-succeeded` / `task-failed` (the
worker already emits these: `worker_send_task_events=True` + `task_track_started`),
maintains the live node-state map, and **pushes a delta** on each transition to
the Vinyl server → `hub.emit` → WS → spinning ring. The browser is pure push;
there is no polling anywhere in the live lane.

**Why a receiver, not Beat.** A receiver is a *consumer* (like Flower itself),
not a task on a queue — so it is **not** subject to the solo-worker trap: nothing
to queue behind the ~60s `gpu_solve`. It also catches **every** transition the
instant it fires, including sub-100ms NPU `route`/`draft` steps a periodic
sampler skips between ticks (observed live). Beat samples an event stream and
loses fast transitions; it's the wrong abstraction here (see D3).

**Transport (Python receiver → Node dashboard): Redis pub/sub** — the receiver
publishes distilled `{node, tier, state}` onto a Redis channel; the Vinyl server
*subscribes* (push, event-driven on both ends). Reuses the broker, no new port,
a clean JSON contract instead of parsing Celery's wire format in TS.

`flower_activity.snapshot()` stays the live-state read primitive for the
**poll-tolerant** consumers (`cascade_top`, experiments). The dashboard does not
poll it — the receiver's event stream is the dashboard's source. Both are views
of the same underlying worker task-event stream (the true source of truth).

### D3 — Beat is for periodic work, not this signal; persistence is separate

Celery Beat is the ecosystem's cron — right for *scheduled periodic* jobs (a
minutely occupancy rollup, pruning old `.rec`, a heartbeat metric) and we'd reach
for it there. It is **not** the driver for the real-time liveness push: scored on
maintainability (adds a scheduler + an observer worker + schedule state),
durability (adds none), and signal (strictly less — drops sub-tick transitions),
the event receiver wins; they tie only on Celery-nativeness (the receiver is
*more* native — it's the events API Flower is built on).

Persistence is orthogonal: the receiver pushes ephemerally and persists nothing
by default. If a replay ("what was spinning when") or durable experiment feed is
later wanted, the receiver also appends a **separate** stream
(`runs/cascade-live.rec`). YAGNI until that need is real.

## The shape (slice #5 builds against this)

```
                         ┌─ Flower REST → snapshot() → cascade_top / experiments  (poll OK: CLI/batch)
  WORKER ──task events──>│
   (solo)                └─ EVENT RECEIVER (app.events.Receiver, callback/transition)
                            → node-state delta → Redis pub/sub ──push──> Vinyl server
                            → hub.emit → WS ──push──> spinning ring            LIVENESS LANE
  ─────────────────────────────────────────────────────────────────────────────────────
  cascade tasks ──append──> runs/cascade.rec ──tailer──> hub.emit → WS         LEDGER LANE
                                          (existing panel: counts / win-lose / $)
```

## Build order (small iterations)

1. **This doc** — lock the boundary. ✅
2. **Python push producer:** an `app.events.Receiver` process whose task-event
   callbacks maintain a live node-state map and publish deltas to a Redis pub/sub
   channel. Routable + gated (the pure node-state/diff logic is unit-testable;
   the receiver loop is live substrate).
3. **Node push consumer:** the Vinyl server subscribes to that Redis channel and
   feeds node-state into the store → `hub.emit` (a new live region), parallel to
   the `.rec` tailer's `onRecord`. TS — vitest/tsc.
4. **`dashboard/src/flow.ts` spinning-ring** on the active node, distinct from
   the post-completion "hot" blip. TS — vitest/tsc/eslint, not the Python cascade
   gate (the edge-verify TS gap).

## Non-goals

- Persisting the live stream (D3 — only if a replay/experiment need appears).
- Replacing `.rec` as the system of record (it stays authoritative for outcomes).
- Using Beat for the live push (rejected, D3) — reserved for periodic work only.
