"""Tests for the EI-1 direct-drive SDXL client.

The HTTP call is mocked via pytest-mock so the tests never touch the network
or require the SDXL server to be running. Coverage targets the pure helpers
(`build_spec`, `format_result`, `format_health`, `parse_args`) plus `run()`
end-to-end through a mocked `httpx.post`/`httpx.get`.
"""
from __future__ import annotations

import argparse

import httpx
import pytest

from scripts.sdxl import (
    build_spec,
    format_health,
    format_result,
    get_health,
    parse_args,
    post_generate,
    run,
)

# ---- parse_args ------------------------------------------------------------


def test_parse_args_minimal():
    args = parse_args(["a cat"])
    assert args.prompt == "a cat"
    assert args.negative is None
    assert args.steps is None
    assert args.guidance is None
    assert args.seed is None
    assert args.width is None
    assert args.height is None
    assert args.size is None
    assert args.health is False


def test_parse_args_health_makes_prompt_optional():
    args = parse_args(["--health"])
    assert args.prompt is None
    assert args.health is True


def test_parse_args_all_knobs():
    args = parse_args([
        "an apple", "--negative", "blur", "--steps", "40",
        "--guidance", "7.5", "--seed", "42",
        "--width", "1024", "--height", "768",
        "--server", "http://example:8188",
    ])
    assert args.prompt == "an apple"
    assert args.negative == "blur"
    assert args.steps == 40
    assert args.guidance == 7.5
    assert args.seed == 42
    assert args.width == 1024
    assert args.height == 768
    assert args.server == "http://example:8188"


# ---- build_spec ------------------------------------------------------------


def _ns(**overrides) -> argparse.Namespace:
    """Defaults match parse_args output for prompt-only invocation."""
    base = dict(prompt="x", negative=None, steps=None, guidance=None,
                seed=None, width=None, height=None, size=None,
                server="http://localhost:8188", health=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_spec_minimal_only_carries_prompt():
    """Optional fields stay out of the spec so the server applies its own
    config-driven defaults (which already track env-overrides)."""
    spec = build_spec(_ns(prompt="cat"))
    assert spec == {"prompt": "cat"}


def test_build_spec_all_fields_propagate():
    spec = build_spec(_ns(
        prompt="cat", negative="blur", steps=40, guidance=7.5, seed=42,
        width=1024, height=768,
    ))
    assert spec == {
        "prompt": "cat", "negative_prompt": "blur",
        "width": 1024, "height": 768,
        "steps": 40, "guidance_scale": 7.5, "seed": 42,
    }


def test_build_spec_size_sets_both_dims():
    spec = build_spec(_ns(prompt="cat", size=768))
    assert spec == {"prompt": "cat", "width": 768, "height": 768}


def test_build_spec_explicit_width_height_win_over_size():
    """`--width/--height` are more specific than `--size`."""
    spec = build_spec(_ns(prompt="cat", size=768, width=1024, height=512))
    assert spec["width"] == 1024
    assert spec["height"] == 512


def test_build_spec_only_width_falls_back_to_size_for_height():
    """Partial override: --width set, --height unset, --size falls through."""
    spec = build_spec(_ns(prompt="cat", size=768, width=1024))
    assert spec["width"] == 1024
    assert spec["height"] == 768


# ---- formatters -----------------------------------------------------------


def test_format_result_success_one_line():
    out = format_result({
        "available": True, "path": "runs/artifacts/x.png",
        "seed": 42, "latency_s": 12.3,
    })
    assert out == "wrote: runs/artifacts/x.png (seed=42, latency=12.3s)"


def test_format_result_unavailable_surfaces_error():
    payload = {"available": False, "error": "model not loaded"}
    out = format_result(payload)
    assert "available:false" in out
    assert "model not loaded" in out


def test_format_result_handles_missing_fields():
    """A malformed-but-200 response shouldn't crash; defaults to '?'."""
    out = format_result({"available": True})
    assert "wrote: ?" in out
    assert "seed=?" in out


def test_format_health_ready():
    out = format_health({"available": True, "model": "sdxl-base",
                         "device": "cuda", "artifacts": "runs/artifacts"})
    assert out.startswith("[READY]")
    assert "sdxl-base" in out
    assert "cuda" in out


def test_format_health_not_ready():
    out = format_health({"available": False})
    assert out.startswith("[NOT READY]")


# ---- post_generate (mocked httpx) -----------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=httpx.Request("POST", "http://example"),
                response=httpx.Response(self.status_code, text=self.text),
            )


def test_post_generate_round_trips(mocker):
    mocker.patch("scripts.sdxl.httpx.post",
                 return_value=_FakeResponse(200, {"available": True, "path": "p"}))
    result = post_generate("http://localhost:8188", {"prompt": "x"})
    assert result == {"available": True, "path": "p"}


def test_post_generate_strips_trailing_slash(mocker):
    """The URL stays canonical even if the user passes `http://host/`.

    Also pins the long POST timeout (600s) -- SDXL inference is the slow
    path (30-step 1024^2 ~15-30s on the 5070 Ti), and a regression to
    httpx's 5s default would silently start timing out every call."""
    sent = {}

    def fake_post(url, json, timeout):
        sent["url"] = url
        sent["timeout"] = timeout
        return _FakeResponse(200, {"available": True, "path": "p"})

    mocker.patch("scripts.sdxl.httpx.post", side_effect=fake_post)
    post_generate("http://localhost:8188/", {"prompt": "x"})
    assert sent["url"] == "http://localhost:8188/generate"
    assert sent["timeout"] >= 300.0, (
        f"POST timeout regressed to {sent['timeout']}s -- "
        "SDXL inference needs ~30s, httpx default 5s would silently fail"
    )


def test_post_generate_connection_error_translates_to_runtime_error(mocker):
    """ConnectError gets remapped to a RuntimeError with a remediation hint."""
    mocker.patch("scripts.sdxl.httpx.post",
                 side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(RuntimeError) as exc:
        post_generate("http://localhost:8188", {"prompt": "x"})
    assert "uvicorn scripts.image_server:app" in str(exc.value)


def test_post_generate_http_500_surfaces(mocker):
    """Non-connect HTTP errors propagate so callers see the server's message."""
    mocker.patch("scripts.sdxl.httpx.post",
                 return_value=_FakeResponse(500, {"error": "oom"}))
    with pytest.raises(httpx.HTTPStatusError):
        post_generate("http://localhost:8188", {"prompt": "x"})


# ---- get_health (mocked httpx) --------------------------------------------


def test_get_health_round_trips(mocker):
    """Round-trip + pin the 10s GET timeout (health is cheap; default 5s
    would risk false-negatives during the model-load warmup window)."""
    sent = {}

    def fake_get(url, timeout):
        sent["url"] = url
        sent["timeout"] = timeout
        return _FakeResponse(200, {"available": True})

    mocker.patch("scripts.sdxl.httpx.get", side_effect=fake_get)
    assert get_health("http://localhost:8188") == {"available": True}
    assert sent["timeout"] >= 5.0, (
        f"GET timeout regressed to {sent['timeout']}s -- "
        "health may need >5s during model-load warmup"
    )


def test_get_health_connection_error_translates(mocker):
    mocker.patch("scripts.sdxl.httpx.get",
                 side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(RuntimeError) as exc:
        get_health("http://localhost:8188")
    assert "uvicorn scripts.image_server:app" in str(exc.value)


def test_get_health_http_error_propagates(mocker):
    mocker.patch("scripts.sdxl.httpx.get",
                 return_value=_FakeResponse(500))
    with pytest.raises(httpx.HTTPStatusError):
        get_health("http://localhost:8188")


# ---- run() end-to-end -----------------------------------------------------


def test_run_happy_path(mocker, capsys):
    mocker.patch("scripts.sdxl.httpx.post", return_value=_FakeResponse(200, {
        "available": True, "path": "runs/artifacts/x.png",
        "seed": 7, "latency_s": 12.3,
    }))
    code = run(["a cat"])
    assert code == 0
    out = capsys.readouterr().out
    assert "wrote: runs/artifacts/x.png" in out


def test_run_health_path(mocker, capsys):
    mocker.patch("scripts.sdxl.httpx.get", return_value=_FakeResponse(200, {
        "available": True, "model": "sdxl-base", "device": "cuda",
        "artifacts": "runs/artifacts",
    }))
    code = run(["--health"])
    assert code == 0
    assert "[READY]" in capsys.readouterr().out


def test_run_missing_prompt_without_health_exits_2(capsys):
    """No prompt and no --health is a usage error -- exit code 2."""
    code = run([])
    assert code == 2
    assert "prompt is required" in capsys.readouterr().err


def test_run_connection_refused_prints_remediation(mocker, capsys):
    mocker.patch("scripts.sdxl.httpx.post",
                 side_effect=httpx.ConnectError("connection refused"))
    code = run(["a cat"])
    assert code == 1
    err = capsys.readouterr().err
    assert "not reachable" in err
    assert "uvicorn scripts.image_server:app" in err


def test_run_server_returns_available_false_exits_1(mocker, capsys):
    """A 200 OK with available:false isn't a transport error but still a
    failure for the caller -- the script must signal it via exit code."""
    mocker.patch("scripts.sdxl.httpx.post", return_value=_FakeResponse(200, {
        "available": False, "error": "model not loaded",
    }))
    code = run(["a cat"])
    assert code == 1
    assert "available:false" in capsys.readouterr().out


def test_run_http_error_propagates(mocker, capsys):
    mocker.patch("scripts.sdxl.httpx.post",
                 return_value=_FakeResponse(500, {"error": "oom"}))
    code = run(["a cat"])
    assert code == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_run_health_connection_refused(mocker, capsys):
    mocker.patch("scripts.sdxl.httpx.get",
                 side_effect=httpx.ConnectError("connection refused"))
    code = run(["--health"])
    assert code == 1
    assert "not reachable" in capsys.readouterr().err
