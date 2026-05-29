# FINDINGS — Canvas repair-loop via worker-retry (de-risk spike)

**Date:** 2026-05-28 · **Branch:** `feat/canvas-repair-retry-spike` · **Status:** PROVEN — eager AND live-broker agree

## The one question

Can a single bound Celery task drive the bounded repair loop — `generate → gate
→ retry-if-FAIL, up to cap` — with the cap **structurally enforced**, the round
counter intact, and per-attempt `.rec` telemetry surviving, behind a single
client-side `.get()`? If yes, the "agent composes a topology, blocks once, every
step is a task" model (`DESIGN-celery-canvas.md`) is unblocked.

## Design under test (`cascade/canvas_spike.py`)

- A gate FAIL is a normal return value in the cascade, not an exception. To use
  Celery's retry machinery we **model it as one**: a failed gate raises
  `self.retry(exc=GateFailed(prior, failures), kwargs={...prior...})`.
- **The cap is a pre-check, not exhaustion-handling.** `if self.request.retries
  >= self.max_retries: return _capped(...)`. This is deliberate: `self.retry(
  exc=...)` re-raises *that exc* (not `MaxRetriesExceededError`) on exhaustion,
  and the behaviour differs eager vs broker. The pre-check sidesteps the
  ambiguity entirely and makes `.get()` **always return a dict, never raise**.
- `self.request.retries` (0 on first run, +1 per retry) **is** the round counter
  — Celery threads it for us, so `repair_cap` / `over_cap_episodes` stay
  meaningful with no hand-rolled counter.
- Reuses the existing `@recorded` worker fns (`tasks.generate`,
  `tasks.verify_functional`) verbatim → **record-then-raise**: both write their
  `.rec` line and return *before* the task raises the retry.
- `solve_balanced()` is the only `.get()` — at the **client** boundary, never
  inside a task (avoids the worker-blocking-on-children anti-pattern).

## Result — eager path (PROVEN)

`tests/test_canvas_spike.py`, run eager (`task_always_eager`), tier calls
replaced with scripted spies (no Ollama, no broker). **6/6 pass; module at 100%.**

| Test | Asserts |
|---|---|
| pass first try | `final_tier=gpu`, `rounds=0`, 1 generate / 1 verify |
| pass after 1 repair | `rounds=1`, 2 generate / 2 verify |
| **always fail holds the cap** | `final_tier=capped->tier3`, `rounds=cap`, **exactly `cap+1` generate calls — not one more** |
| gpu unavailable caps immediately | cap signal, verify never called |
| repair threads prior forward | call 0 has `prior_attempt=None`; call *i* repairs on draft *i-1* |
| get-exhaustion is capped | broker-path `MaxRetriesExceededError` → cap signal, not a raise |

**Headline:** under eager execution `self.retry()` loops synchronously, the
counter increments, and the cap holds at `cap+1` structurally. A `(cap+1)`'th
repair is impossible — the invariant the 2026-05-20 log breach violated when the
cap lived only as a CLAUDE.md prompt rule.

## Result — live-broker path (PROVEN, agrees with eager)

The spike's stated risk is **eager ≠ broker**. Confirmed against **real Redis +
a live solo worker + real Ollama (`qwen2.5-coder:14b`)**, driving an
impossible-DSL task (`assert add_numbers(1,1)==2` AND `==3` — fails every round
regardless of model output):

```
docker compose up -d redis
python -m celery -A cascade.celery_app worker -I cascade.canvas_spike -Q gpu,verify --pool=solo -l info
# client: solve_balanced(<task>, dsl=<contradictory>)
```

Worker log (one task UUID, retries in-place):
```
gpu_solve_task[9892ade3-…] received
gpu_solve_task[9892ade3-…] retry: Retry in 0s: GateFailed('gate failed (1 failure(s))')
gpu_solve_task[9892ade3-…] received
gpu_solve_task[9892ade3-…] retry: Retry in 0s: GateFailed('gate failed (1 failure(s))')
gpu_solve_task[9892ade3-…] received
gpu_solve_task[9892ade3-…] succeeded: {'final_tier': 'capped->tier3', 'rounds': 2, ...}
```

Verified:
- **Exactly `cap+1` = 3 executions** of `gpu_solve_task` (same UUID → retries of
  one task, not new tasks), and **no 4th**. Cap holds in a real worker.
- **`Retry in 0s`** — `countdown=0` works; no 180s `default_retry_delay` stall.
- **`.rec` deltas: `edge-gpu.rec` 239→242 (+3), `edge-verify.rec` 599→602 (+3)**
  — one record per attempt per tier. Record-then-raise survives the broker.
- **`.get()` returned `{'final_tier':'capped->tier3','rounds':2}`** — a dict, no
  raise; `self.request.retries` carried across redeliveries as the round counter.

**Eager and broker AGREE.** The one divergence the broker path surfaced was the
`default_retry_delay=180s` backoff (invisible eager), fixed with `countdown=0`.

### Windows note
`celery.exe` console-script is blocked by Windows Application Control (os error
4551). Launch via `python -m celery …`; use `--pool=solo` (prefork/billiard is
unreliable on Windows).

## Scope / boundaries (unchanged from the agreed scope)

In: the GPU repair loop only, entering at a fresh generate. **Out:** NPU
`route`/`draft` tasks, `chord`/`group`/speculation, hardware pinning, multi-box,
agent-dynamic composition, the nested three-graph chain — all ride on *this*
working first. Nothing on the hot path imports `canvas_spike`; opt-in
(`uv sync --extra celery`).

## Verdict

The hard part I'd flagged as "the unsolved crux" is tractable and **proven on
both execution paths**: **worker-retry + exception-as-gate-FAIL + a
`self.request.retries` cap pre-check** expresses the bounded loop cleanly, with
the cap structurally enforced, the counter free, and telemetry intact, eager and
broker alike. The Canvas port (lift `route`/`draft`/`cloud` into tasks; agent
composes the topology; nested graphs) is unblocked — this was the load-bearing
unknown.
