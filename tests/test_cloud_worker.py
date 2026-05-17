"""cloud_worker reaches true 100% with a stubbed Anthropic client -- no
network, no spend. `_generate` takes the client as a parameter, so the stub
is injected directly (no monkeypatching). CONFIG is swapped (via pytest-mock)
for a namespace so the gating/status logic can be driven directly."""
import os
import types

import anthropic
import httpx
import pytest

from cascade import cloud_worker
from cascade.cloud_worker import (
    CloudResult,
    _compose_user,
    _generate,
    _price_for,
    est_cost_usd,
    make_cloud_worker,
    reason_note,
)


def _cfg(key=None):
    return types.SimpleNamespace(
        anthropic_api_key=key, cloud_model="m", cloud_max_tokens=128
    )


# --- fake Anthropic client -------------------------------------------------

class _Blk:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Stream:
    def __init__(self, msg):
        self._m = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._m


class _Messages:
    def __init__(self, msg=None, exc=None):
        self._msg, self._exc = msg, exc

    def stream(self, **kw):
        if self._exc:
            raise self._exc
        return _Stream(self._msg)


class _Client:
    def __init__(self, msg=None, exc=None):
        self.messages = _Messages(msg, exc)


# --- CloudResult: behavior moved off the dataclass to functions ------------

@pytest.mark.parametrize(
    "model, in_rate, out_rate",
    [
        ("claude-opus-4-7", 15.0, 75.0),       # version bump still matches prefix
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-haiku-4-5", 1.0, 5.0),
        ("claude-3-opus-20240229", 15.0, 75.0),
        ("claude-3-5-sonnet-latest", 3.0, 15.0),
        ("gpt-4o", 15.0, 75.0),                # UNKNOWN -> dearest known rate
        ("", 15.0, 75.0),                      # empty -> dearest, never cheap
    ],
)
def test_price_for_table_and_conservative_fallback(model, in_rate, out_rate):
    assert _price_for(model) == (in_rate, out_rate)


def test_reason_note_and_cost():
    # Known model: priced at its own rate.
    ok = CloudResult("txt", 1.0, "claude-sonnet-4-6", True, 1_000_000, 1_000_000)
    assert reason_note(ok) == "ok"
    assert est_cost_usd(ok) == pytest.approx(18.0)         # 3 + 15
    # Unknown model "m": the latent-bug fix -- costed at the dearest known
    # (Opus) rate so the credit guard can never under-count a new model.
    unknown = CloudResult("txt", 1.0, "m", True, 1_000_000, 1_000_000)
    assert est_cost_usd(unknown) == pytest.approx(90.0)    # 15 + 75
    bad = CloudResult("boom", 0.0, "m", False)
    assert reason_note(bad) == "boom"
    assert est_cost_usd(bad) == 0.0


def test_compose_user_branches():
    assert _compose_user("q", None) == "q"
    assert _compose_user("q", "") == "q"
    out = _compose_user("q", "PRIOR")
    assert "q" in out and "failed verification" in out and "PRIOR" in out


# --- gating / status (make_cloud_worker) -----------------------------------

def test_no_key_disables_and_generate_noops(mocker):
    mocker.patch.object(cloud_worker, "CONFIG", _cfg(key=None))
    w = make_cloud_worker(enabled=True)
    assert w.enabled is False
    assert w.status == "disabled (no ANTHROPIC_API_KEY)"
    r = w.generate("q")
    assert r.available is False and r.text == "[paid cloud tier disabled]"


def test_key_present_but_not_enabled(mocker):
    mocker.patch.object(cloud_worker, "CONFIG", _cfg(key="k"))
    w = make_cloud_worker(enabled=False)
    assert w.enabled is False
    assert w.status == ("disabled (key present; pass --cloud / "
                        "enable_cloud=True)")


def test_enabled_construction(mocker):
    mocker.patch.object(cloud_worker, "CONFIG", _cfg(key="k"))
    mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"})
    w = make_cloud_worker(enabled=True)
    assert w.enabled is True
    assert w.status == "enabled (m)"
    assert w.model == "m" and callable(w.generate)


# --- _generate (stubbed client injected directly) --------------------------

def test_generate_success_counts_usage():
    msg = types.SimpleNamespace(
        content=[_Blk("thinking"), _Blk("text", "hello")],
        usage=types.SimpleNamespace(
            input_tokens=10, cache_read_input_tokens=2,
            cache_creation_input_tokens=1, output_tokens=5),
    )
    r = _generate(_Client(msg=msg), "m", 128, "q", "p")
    assert r.available and r.text == "hello" and r.model == "m"
    assert r.input_tokens == 13 and r.output_tokens == 5


def test_generate_handles_missing_usage():
    r = _generate(
        _Client(msg=types.SimpleNamespace(
            content=[_Blk("text", "z")], usage=None)),
        "m", 128, "q",
    )
    assert r.text == "z" and r.input_tokens == 0 and r.output_tokens == 0


def test_generate_handles_api_error():
    err = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    r = _generate(_Client(exc=err), "m", 128, "q")
    assert r.available is False and r.text.startswith("[cloud error:")
