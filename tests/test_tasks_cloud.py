"""Tests for the Tier-3 cloud task (`cloud_generate`) -- Canvas Phase 1 Slice 2.

Strategy: mock `cascade.tasks._cloud.generate` so no real Anthropic API call
runs. Two layers exercised separately:

- The recorded fn `cloud_generate`: shape contract on the returned dict,
  including the disabled hand-off (`available:false`, `est_cost_usd=0.0`)
  and the cost arithmetic on a healthy result.
- The Celery wrapper `cloud_generate_task` under `task_always_eager`: same
  shape round-trips, plus the queue pin (`cloud`) and stable task name
  (`mesh.cloud_generate`) the Canvas chain will reference in Slice 3.

The structural spend invariant -- no worker subscribes to the `cloud` queue
by default, so `apply_async()` enqueues but never runs -- is a property of
the documented worker launch (`-Q npu,gpu,verify`), NOT something a unit
test can prove. Slice 4's findings doc covers the live-broker assertion.
"""
from __future__ import annotations

import pytest

# Celery is an opt-in extra (`uv sync --extra celery`); CI installs only
# `--extra mcp`. Skip cleanly when celery isn't available so the collection
# error doesn't redden the build (lesson from spike PR #83 / fix PR #86).
pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import tasks  # noqa: E402  (skip-gated import)
from cascade.celery_app import app  # noqa: E402
from cascade.cloud_worker import CloudResult  # noqa: E402


@pytest.fixture
def eager():
    """Run Celery tasks inline so no broker is required."""
    prev = app.conf.task_always_eager
    app.conf.task_always_eager = True
    try:
        yield
    finally:
        app.conf.task_always_eager = prev


def _result(*, text="OK", available=True, latency=1.23,
            model="claude-opus-4-7", in_tok=0, out_tok=0):
    """A canonical CloudResult; defaults to a healthy success with zero
    tokens so cost arithmetic stays predictable in tests that don't care."""
    return CloudResult(text=text, latency_s=latency, model=model,
                       available=available, input_tokens=in_tok,
                       output_tokens=out_tok)


def _patch_cloud(mocker, *, generate_ret):
    """Replace module-level `_cloud` with a Mock whose `.generate` returns
    `generate_ret`. CloudWorker is frozen, so we can't attach attrs to the
    real `_cloud` -- swap the whole handle instead."""
    fake = mocker.Mock()
    fake.generate.return_value = generate_ret
    fake.enabled = generate_ret.available
    fake.model = generate_ret.model
    mocker.patch("cascade.tasks._cloud", fake)
    return fake


def test_cloud_generate_disabled_returns_handoff(mocker):
    """When the worker is disabled (no key OR enable_cloud=False), generate
    returns a `CloudResult(available=False)` with the disabled-text in
    `text`; cloud_generate translates that into the standard hand-off shape
    with cost zero. NEVER raises -- a down tier is a status, not an error."""
    _patch_cloud(mocker, generate_ret=_result(
        text="[paid cloud tier disabled]", available=False, latency=0.0))
    r = tasks.cloud_generate(prompt="x")
    assert r["available"] is False
    assert r["text"] == "[paid cloud tier disabled]"
    assert r["latency_s"] == 0.0
    assert r["input_tokens"] == 0
    assert r["output_tokens"] == 0
    assert r["est_cost_usd"] == 0.0
    # reason_note returns text when not available -- the disabled message is
    # itself the diagnostic, so reason==text on this path.
    assert r["reason"] == "[paid cloud tier disabled]"


def test_cloud_generate_enabled_returns_shape(mocker):
    """A healthy enabled call returns
    {available, text, model, latency_s, input_tokens, output_tokens,
     est_cost_usd, reason}. latency rounded to 2dp; cost rounded to 6dp."""
    _patch_cloud(mocker, generate_ret=_result(
        text="```python\nprint('hi')\n```", latency=1.2345,
        in_tok=100, out_tok=50))
    r = tasks.cloud_generate(prompt="say hi")
    assert r == {
        "available": True,
        "text": "```python\nprint('hi')\n```",
        "model": "claude-opus-4-7",
        "latency_s": 1.23,
        "input_tokens": 100,
        "output_tokens": 50,
        # Opus rates: $15/M in, $75/M out -> 100/1e6*15 + 50/1e6*75 = 0.00525.
        "est_cost_usd": 0.00525,
        "reason": "ok",
    }


def test_cloud_generate_cost_uses_dearest_known_rate_on_unknown_model(mocker):
    """An unrecognised model is billed at the dearest known rate (Opus) so
    a new model release can never silently under-count spend. Pinned here
    because the credit-guard upstream depends on this conservative bias."""
    _patch_cloud(mocker, generate_ret=_result(
        model="claude-future-1", in_tok=1_000_000, out_tok=0))
    r = tasks.cloud_generate(prompt="x")
    # Dearest known input rate = $15/M (Opus); 1M in tokens -> $15.0.
    assert r["est_cost_usd"] == 15.0


def test_cloud_generate_forwards_prior_attempt(mocker):
    """The repair / Canvas-chain hand-off threads the failed lower-tier draft
    forward via `prior_attempt`. Pinned so a refactor doesn't silently drop
    it (the cloud's repair quality depends on having the bad draft to diagnose)."""
    fake = _patch_cloud(mocker, generate_ret=_result(text="fixed"))
    tasks.cloud_generate(prompt="task", prior_attempt="bad draft")
    fake.generate.assert_called_once_with("task", prior_attempt="bad draft")


def test_cloud_generate_task_eager_round_trips(eager, mocker):
    """The @app.task wrapper runs cloud_generate inline under
    task_always_eager and returns the dict unchanged."""
    _patch_cloud(mocker, generate_ret=_result(
        text="ans", latency=0.5, in_tok=10, out_tok=5))
    r = tasks.cloud_generate_task.apply(args=["x"]).get()
    assert r["available"] is True
    assert r["text"] == "ans"
    assert r["est_cost_usd"] > 0


def test_cloud_generate_task_forwards_prior_attempt(eager, mocker):
    """`cloud_generate_task` accepts the same `prior_attempt` arg as
    `cloud_generate` and routes it through to the worker."""
    fake = _patch_cloud(mocker, generate_ret=_result(text="repaired"))
    tasks.cloud_generate_task.apply(args=["task", "bad draft"]).get()
    fake.generate.assert_called_once_with("task", prior_attempt="bad draft")


def test_cloud_generate_task_is_queue_pinned_to_cloud():
    """`cloud_generate_task.queue == 'cloud'` is the structural spend
    invariant: the documented worker launch
    `python -m celery -A cascade.celery_app worker -Q npu,gpu,verify` does
    NOT include `cloud`, so dispatched cloud tasks enqueue but never run --
    same guarantee as today's `--strict-mcp-config` exclusion. The task
    `name` is the stable contract Slice 3's `balanced_signature` will
    reference."""
    assert tasks.cloud_generate_task.queue == "cloud"
    assert tasks.cloud_generate_task.name == "mesh.cloud_generate"
