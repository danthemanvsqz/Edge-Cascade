"""Outside-in BEHAVIORAL probes against the LIVE Canvas substrate.

These tests drive the *real* running mesh -- a real Redis broker + a Celery
worker on `npu,gpu,verify` + real NPU/GPU/verify hardware. No mocks, no
embedded `memory://` worker (that is `test_canvas_balanced_integration.py`'s
job), no eager mode (that is `test_canvas_balanced.py`'s job). Every test
routes a genuine task through `solve_budget_canvas` and asserts on the
*behavior* the mesh is contractually obliged to deliver -- never on trace
strings or any other implementation detail. The `mesh.Outcome` and the
win/lose `.rec` log ARE the contract; the trace is mechanism and is
deliberately not asserted, so the #9 `_draft_gate` decompose is free to
rewrite trace wording without touching these pins.

WHY A LIVE LAYER AT ALL (what the mocked layers structurally cannot prove):
  * concurrent routing against the real broker (envelope isolation + the
    byte-framed log surviving concurrent appends),
  * exactly-once self-logging across the real `self.replace()` GPU handoff,
  * degenerate input (empty query) terminating on real route/draft/gate,
  * the bounded cap + no-cloud-spend invariants holding on real hardware.

Opt-in: `uv run pytest -m live --no-cov tests/test_canvas_live_behavior.py`.
AUTO-SKIPS (exit-clean) when no live worker is up, so CI and hardware-less
boxes are never blocked -- same contract as scripts/e2e_local.py. NOT in the
default `pytest` run (pyproject `addopts` excludes `-m live`) and NOT coverage-
tracked (the celery substrate is in `[tool.coverage.run] omit`).
"""
from __future__ import annotations

import concurrent.futures as cf

import pytest

pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import canvas_client, logfmt, mesh  # noqa: E402
from cascade.celery_app import app as celery_app  # noqa: E402
from cascade.config import CONFIG  # noqa: E402

pytestmark = pytest.mark.live

_REQUIRED_QUEUES = {"npu", "gpu", "verify"}


@pytest.fixture(scope="module", autouse=True)
def _require_live_worker():
    """Skip the whole module unless a live worker is consuming every queue the
    budget chain dispatches to. Mirrors e2e_local.py's skip contract: a box
    without the running substrate is never failed, only skipped.

    `inspect.active_queues()` maps worker-name -> [queue-dicts]; we flatten to
    the set of queue NAMES across all workers (the draft from the pipe checked
    `'npu' in active_queues`, which is wrong -- the keys are worker names)."""
    reply = celery_app.control.inspect(timeout=3).active_queues()
    consumed = {
        q["name"]
        for queues in (reply or {}).values()
        for q in queues
    }
    missing = _REQUIRED_QUEUES - consumed
    if missing:
        pytest.skip(
            f"no live worker consuming {sorted(missing)} "
            f"(found {sorted(consumed) or 'none'}); "
            f"start it with scripts\\edge-cli.ps1 -Canvas"
        )


def _records() -> list[dict[str, str]]:
    """Parse the (test-isolated) win/lose `.rec` with the SAME deterministic
    parser the dashboard uses. conftest's `_isolate_cascade_rec` autouse
    fixture redirects `canvas_client._cascade_rec_path` to a per-test tmp file,
    so we read it via the module attribute (NOT a direct import) for the
    monkeypatch to take effect -- otherwise this would read the real
    runs/cascade.rec."""
    path = canvas_client._cascade_rec_path()
    if not path.exists():
        return []
    return logfmt.parse_stream(path.read_bytes())


# ---------------------------------------------------------------------------
# B0 -- the load-bearing behaviors the #9 refactor must preserve, pinned LIVE.
# ---------------------------------------------------------------------------


def test_easy_wellformed_task_resolves_at_a_local_tier_without_spend():
    """GIVEN an easy, well-formed coding task,
    WHEN the live mesh solves it,
    THEN it resolves at a LOCAL tier (npu or gpu) -- never capped, never the
    paid cloud tier."""
    oc = canvas_client.solve_budget_canvas(
        "write a python function add(a, b) that returns their sum"
    )
    assert isinstance(oc, mesh.Outcome)
    assert oc.resolved and not oc.capped, (
        f"an easy well-formed task did NOT resolve on the live mesh "
        f"(resolved={oc.resolved}, capped={oc.capped}, "
        f"final_tier={oc.final_tier!r}); the cheapest-sufficient-tier "
        f"contract was not met"
    )
    assert oc.final_tier in {"npu", "igpu", "gpu"}, (
        f"resolved at {oc.final_tier!r}, expected a local tier -- an easy "
        f"task should never reach the paid cloud tier"
    )


def test_ungateable_task_caps_bounded_and_never_spends_cloud():
    """GIVEN a task that cannot produce gateable code (prose-only),
    WHEN the live mesh exhausts the locals,
    THEN it terminates by handing off to Tier-3 within the bounded repair cap
    -- it does not loop past the cap, hang, or silently escalate off-box to the
    paid cloud tier."""
    oc = canvas_client.solve_budget_canvas(
        "Reply with exactly one plain English sentence and absolutely no code, "
        "code fences, or programming syntax of any kind: explain why the sky "
        "appears blue."
    )
    assert oc.capped and not oc.resolved, (
        f"a provably-ungateable task did NOT cap (resolved={oc.resolved}, "
        f"capped={oc.capped}, final_tier={oc.final_tier!r}); the mesh either "
        f"accepted non-code as an answer or failed to terminate the locals"
    )
    assert oc.final_tier == "capped->tier3", (
        f"cap handed off to {oc.final_tier!r}, not 'capped->tier3'"
    )
    assert oc.final_tier != "cloud", (
        "SPEND INVARIANT BREACH: an unsolved task escalated to the paid cloud "
        "tier on the default (no-cloud) worker"
    )
    assert 0 <= oc.repair_rounds <= CONFIG.repair_cap, (
        f"CAP INVARIANT BREACH: {oc.repair_rounds} repair rounds ran, but the "
        f"structural cap is {CONFIG.repair_cap} -- the bounded loop was exceeded"
    )


def test_skip_draft_route_stays_bounded_and_never_spends():
    """GIVEN a long, hard prompt the router rates above the skip-draft
    threshold (so the NPU draft is skipped and there is NO prior to repair),
    WHEN the live mesh runs the GPU phase from scratch (round_base=0),
    THEN the run stays within the repair cap and never escalates to the paid
    cloud tier -- whichever way it resolves.

    This is a structurally DIFFERENT loop from B0b's failed-draft path: with no
    prior the first GPU call is a fresh `generate` (round 0) followed by the
    bounded repairs, a path the eager unit tests note can issue cap+1 GPU calls.
    The cap invariant ('repair_rounds never exceeds the cap') must hold here on
    real hardware too."""
    long_hard = (
        "Design and fully implement, in a single answer, a production-grade "
        "distributed rate limiter that supports sliding-window and token-bucket "
        "policies across multiple processes, with pluggable Redis and in-memory "
        "backends, graceful degradation when the backend is unreachable, and "
        "per-tenant configuration hot-reloaded without dropping in-flight "
        "requests. Explain every concurrency trade-off you make."
    )
    oc = canvas_client.solve_budget_canvas(long_hard)
    assert isinstance(oc, mesh.Outcome)
    assert oc.resolved ^ oc.capped, (
        f"a skip-draft route returned an indecisive Outcome "
        f"(resolved={oc.resolved}, capped={oc.capped})"
    )
    assert oc.repair_rounds <= CONFIG.repair_cap, (
        f"CAP INVARIANT BREACH on the no-prior path: {oc.repair_rounds} repair "
        f"rounds ran with a structural cap of {CONFIG.repair_cap}"
    )
    assert oc.final_tier != "cloud", (
        "SPEND INVARIANT BREACH: a skip-draft route escalated to the paid "
        "cloud tier on the default (no-cloud) worker"
    )


# ---------------------------------------------------------------------------
# B1 -- concurrent routing: log integrity + envelope isolation. The mocked
# layers run one chain at a time; only the live broker interleaves chains and
# does concurrent appends to the byte-length-framed .rec.
# ---------------------------------------------------------------------------


def test_concurrent_routes_keep_the_winloss_log_parseable_and_isolated():
    """GIVEN several distinct tasks dispatched concurrently from one client,
    WHEN they all route through the live worker at once,
    THEN (a) each returns its own cleanly-resolved-or-capped Outcome, (b) the
    byte-framed win/lose log holds exactly one parseable record per route (no
    desync from concurrent appends), and (c) no two routes returned the same
    answer (no envelope cross-talk between interleaved chains)."""
    prompts = {
        "add": "write a python function add(a, b) that returns their sum",
        "sub": "write a python function sub(a, b) that returns a minus b",
        "mul": "write a python function mul(a, b) that returns a times b",
        "neg": "write a python function neg(a) that returns minus a",
        "sq": "write a python function sq(a) that returns a squared",
        "dbl": "write a python function dbl(a) that returns a doubled",
    }
    with cf.ThreadPoolExecutor(max_workers=len(prompts)) as ex:
        futures = {
            ex.submit(canvas_client.solve_budget_canvas, q): name
            for name, q in prompts.items()
        }
        outcomes = {
            futures[f]: f.result(timeout=600)
            for f in cf.as_completed(futures)
        }

    # (a) every concurrent route produced a decisive Outcome.
    for name, oc in outcomes.items():
        assert isinstance(oc, mesh.Outcome)
        assert oc.resolved ^ oc.capped, (
            f"concurrent route {name!r} returned an Outcome that is neither "
            f"cleanly resolved nor cleanly capped (resolved={oc.resolved}, "
            f"capped={oc.capped}) -- interleaved chains corrupted its state"
        )

    # (b) the framed log survived concurrent appends.
    recs = _records()
    assert len(recs) == len(prompts), (
        f"expected {len(prompts)} parseable win/lose records after "
        f"{len(prompts)} concurrent routes, got {len(recs)} -- the "
        f"byte-length-framed cascade.rec desynced under concurrent appends "
        f"(the win/lose logger is not concurrency-safe)"
    )

    # (c) no envelope cross-talk: distinct prompts -> distinct answers. Two
    # identical answers from distinct prompts would mean an interleaved chain
    # served another chain's envelope.
    answers = [oc.answer for oc in outcomes.values() if oc.resolved]
    assert len(set(answers)) == len(answers), (
        f"two concurrent routes returned identical answers {answers!r} -- "
        f"envelope state bled across interleaved chains on the live broker"
    )


# ---------------------------------------------------------------------------
# B2 -- exactly-once self-logging across the real GPU handoff.
# ---------------------------------------------------------------------------


def test_single_route_appends_exactly_one_winloss_record():
    """GIVEN any single routed task,
    WHEN it finishes (whether resolved or capped),
    THEN exactly ONE record is appended to the win/lose log -- never zero (the
    run silently dropped off the dashboard metric) and never two (the
    self.replace() GPU handoff double-logged)."""
    before = len(_records())
    oc = canvas_client.solve_budget_canvas(
        "write a python function inc(n) that returns n + 1"
    )
    after = len(_records())
    assert after - before == 1, (
        f"a single route appended {after - before} win/lose record(s); the "
        f"logger-last invariant requires exactly one per routed outcome "
        f"(final_tier={oc.final_tier!r})"
    )


# ---------------------------------------------------------------------------
# B3 -- degenerate input. "Should handle but might not": no mocked layer ever
# feeds the real route/draft an empty prompt.
# ---------------------------------------------------------------------------


def test_empty_query_terminates_with_a_decisive_outcome():
    """GIVEN an empty query,
    WHEN the live mesh is asked to solve it,
    THEN it returns a valid Outcome that is cleanly resolved XOR capped within
    the client timeout -- it does not hang (the .get timeout would raise) or
    raise on degenerate input."""
    oc = canvas_client.solve_budget_canvas("")
    assert isinstance(oc, mesh.Outcome)
    assert oc.resolved ^ oc.capped, (
        f"an empty query produced an indecisive Outcome (resolved={oc.resolved}, "
        f"capped={oc.capped}); the mesh should still terminate decisively on "
        f"degenerate input"
    )
