"""Canvas repair-loop spike (de-risk, OPT-IN) -- does Celery worker-retry hold
the bounded repair loop the way the in-process `mesh.solve` does?

This answers ONE question before any real port to Celery Canvas: can a single
bound task drive "generate -> gate -> retry-if-FAIL, up to cap" with

  - the cap STRUCTURALLY enforced (no (cap+1)'th repair, ever),
  - the round counter intact (Celery's own `self.request.retries`),
  - per-attempt `.rec` telemetry surviving (the `@recorded` worker fns run, and
    write, BEFORE any retry is raised -- "record then raise"),

and a SINGLE blocking `.get()` at the client (the agent), with no task ever
calling `.get()` on another task (the worker-blocking-on-children anti-pattern).

A gate FAIL is a normal return value in the cascade, not an exception -- so to
drive the loop with Celery's retry machinery we MODEL it as one: a failed gate
raises a retry. The cap is gated by an explicit `self.request.retries >=
self.max_retries` pre-check that returns a clean capped dict, rather than
relying on `self.retry(exc=...)`'s version-dependent exhaustion behaviour (when
`exc` is passed it re-raises *that*, not `MaxRetriesExceededError`). The
pre-check makes `.get()` ALWAYS return a dict, never raise, and behaves the same
eager (tests) or over a real broker.

Nothing on the pipe/in-process hot path imports this -- it lives beside
`cascade.tasks` (the C1 Phase-0 spike) and reuses its `@recorded` worker calls
verbatim, so a run writes `runs/edge-gpu.rec` / `runs/edge-verify.rec`
byte-identically to the mainline path. NOT wired into `mesh.solve`.

Run (after `docker compose up -d redis` and `uv sync --extra celery`):
    uv run celery -A cascade.celery_app worker -Q gpu,verify -l info
    uv run python -c "from cascade.canvas_spike import solve_balanced; \
        print(solve_balanced('write a python function add(a, b) -> a + b'))"
"""
from __future__ import annotations

from celery.exceptions import MaxRetriesExceededError

from cascade import tasks
from cascade import verifier as syntax_verifier
from cascade.celery_app import app
from cascade.config import CONFIG


class GateFailed(Exception):
    """A candidate failed the verifier gate. Carries the failed draft and the
    structured failures so a retry repairs ON the prior (mirrors mesh.solve's
    `prior`/`failures`). Raised into `self.retry` purely to label the retry in
    Celery's telemetry -- control flow does not depend on catching it, because
    the cap is gated by the `self.request.retries` pre-check below."""

    def __init__(self, prior: str, failures: tuple):
        self.prior = prior
        self.failures = failures
        super().__init__(f"gate failed ({len(failures)} failure(s))")


def _capped(rounds: int, reason: str = "repair cap exhausted") -> dict:
    """Terminal outcome: locals exhausted -> Tier-3 (the agent) takes over. The
    single signal the caller acts on, shaped like the in-process Outcome's
    `final_tier='capped->tier3'`."""
    return {"answer": None, "final_tier": "capped->tier3",
            "rounds": rounds, "reason": reason}


@app.task(bind=True, max_retries=CONFIG.repair_cap, queue="gpu")
def gpu_solve_task(self, query: str, dsl: str | None = None,
                   prior: str | None = None) -> dict:
    """One bounded repair attempt as a retrying task. First execution has
    `prior=None` (fresh generate); each retry carries the previous failed draft
    forward as `prior` (a repair). `self.request.retries` is 0 on the first run
    and increments per retry -- it IS the round counter, threaded by Celery.

    Returns a dict on every terminal path (won or capped); the only thing it
    raises is `self.retry`'s reschedule signal, which Celery handles internally
    and never surfaces to the client's `.get()`."""
    # generate + gate, each via the existing @recorded worker fns -> the .rec
    # write happens HERE, before any retry below ("record then raise").
    gen = tasks.generate(query, prior_attempt=prior)
    if not gen.get("available", True):
        # Tier-2 down (Ollama unreachable) is a hand-off, not an error -- mirror
        # mesh.solve's "gpu unavailable -> capped". No point retrying.
        return _capped(self.request.retries, reason="gpu unavailable")

    # Gate. dsl=None => syntax-only (cascade.verifier.verify), matching the
    # in-process pipe path's mesh.solve. dsl supplied => functional gate
    # (tasks.verify_functional). The parity contract is the same as
    # topologies_canvas._gate, just inlined here so canvas_spike stays
    # self-contained (no dependency on the higher chain module).
    if dsl:
        verdict = tasks.verify_functional(gen["text"], dsl)
        passed = bool(verdict.get("passed"))
        failures = tuple(verdict.get("failures", ()))
    else:
        v = syntax_verifier.verify(gen["text"])
        passed = v.passed
        failures = () if v.passed else (
            {"expr": "syntax", "observed": v.reason,
             "requirement": "fenced Python block that compiles"},
        )
    if passed:
        return {"answer": gen["text"], "final_tier": "gpu",
                "rounds": self.request.retries}

    # Gate FAIL. THE STRUCTURAL CAP: if this run is already the last allowed
    # round, stop -- do NOT schedule another. range-loop parity: with
    # max_retries=cap, runs are retries 0..cap (cap+1 total), so a (cap+1)'th
    # repair is impossible. over_cap_episodes stays meaningful.
    if self.request.retries >= self.max_retries:
        return _capped(self.request.retries)

    # Not capped -> repair on the failed draft. self.retry re-enqueues this same
    # task with `prior` set to the bad draft; raising is how Celery reschedules.
    # kwargs-only (no positional args) so the retry's kwargs replace cleanly
    # without a positional/keyword collision on `query`.
    # countdown=0: a repair is immediate, NOT a transient-failure backoff.
    # Celery's default_retry_delay is 180s -- correct for rate-limit retries,
    # wrong for a repair loop. (Eager execution ignores countdown, so the unit
    # tests pass either way; the broker path is what surfaces this.)
    raise self.retry(
        exc=GateFailed(gen["text"], failures),
        kwargs={"query": query, "dsl": dsl, "prior": gen["text"]},
        countdown=0,
    )


def solve_balanced(query: str, dsl: str | None = None) -> dict:
    """Client-side entry (the agent's ONE blocking call). Dispatches the
    retrying task and blocks on its single result. kwargs-only dispatch keeps
    the retry path collision-free. The task returns a dict on every terminal
    path, so `.get()` returns a dict; the `MaxRetriesExceededError` guard is
    purely defensive against a future code path that lets exhaustion escape."""
    async_result = gpu_solve_task.apply_async(
        kwargs={"query": query, "dsl": dsl})
    try:
        return async_result.get()
    except MaxRetriesExceededError:
        return _capped(CONFIG.repair_cap)
