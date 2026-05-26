"""Format-contract tests for scripts/edge_summary.py (SD-1).

The script's per-tier formatters and payload extractor are pure functions on
dicts -- no MCP roundtrip, no models. We lock down their shape so a silent
drift (e.g. NPU adds a field, summary line goes blank) breaks the build.

The live `_query()` path is intentionally not tested here -- it speaks MCP
stdio against a real subprocess, which is the launcher's smoke domain, not
the unit suite's.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")  # script imports mcp at top; skip if extra absent


def _load_summary_module():
    """Load scripts/edge_summary.py as a module despite the dash-free name
    (it isn't importable from a package; it's a script). Done lazily so the
    importorskip above runs first."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "edge_summary.py"
    spec = importlib.util.spec_from_file_location("edge_summary", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["edge_summary"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def es():
    return _load_summary_module()


# --- _summarize_npu --------------------------------------------------------

def test_npu_available_renders_device_and_caps(es):
    ok, line = es._summarize_npu(
        {"available": True, "device": "GPU.0", "npu_max_tokens": 320}
    )
    assert ok is True
    assert "GPU.0" in line and "320" in line


def test_npu_unavailable_carries_reason(es):
    ok, line = es._summarize_npu(
        {"available": False, "reason": "openvino_genai is required"}
    )
    assert ok is False
    assert "available:false" in line
    assert "openvino_genai" in line


def test_npu_unavailable_without_reason_falls_back(es):
    ok, line = es._summarize_npu({"available": False})
    assert ok is False
    assert "available:false" in line


def test_npu_reason_newlines_collapsed(es):
    _, line = es._summarize_npu({"available": False, "reason": "line1\nline2"})
    assert "\n" not in line


# --- _summarize_gpu --------------------------------------------------------

def test_gpu_available_renders_model(es):
    ok, line = es._summarize_gpu({"available": True, "model": "qwen2.5-coder:14b"})
    assert ok is True
    assert "qwen2.5-coder:14b" in line


def test_gpu_unavailable_says_so(es):
    ok, line = es._summarize_gpu({"available": False})
    assert ok is False
    assert "Ollama" in line or "available:false" in line


# --- _summarize_cloud ------------------------------------------------------

def test_cloud_allowed_is_ready(es):
    ok, line = es._summarize_cloud(
        {"allowed": True, "usd_spent": 0.12, "usd_budget": 5.00}
    )
    assert ok is True
    assert "allowed" in line
    assert "$0.12" in line and "$5.0" in line


def test_cloud_blocked_is_degraded(es):
    ok, line = es._summarize_cloud(
        {"allowed": False, "usd_spent": 0, "usd_budget": 0}
    )
    assert ok is False
    assert "budget-locked" in line


# --- _payload extraction ---------------------------------------------------

class _Result:
    """Minimal stand-in for the MCP CallToolResult shape we read."""

    def __init__(self, structured=None, text=None):
        self.structuredContent = structured
        self.content = [_Text(text)] if text is not None else []


class _Text:
    def __init__(self, t):
        self.text = t


def test_payload_prefers_structured_result(es):
    res = _Result(structured={"result": {"available": True, "x": 1}})
    assert es._payload(res) == {"available": True, "x": 1}


def test_payload_uses_structured_root_when_no_result_key(es):
    res = _Result(structured={"available": False})
    assert es._payload(res) == {"available": False}


def test_payload_falls_back_to_text_json(es):
    res = _Result(text='{"available":true,"device":"NPU"}')
    assert es._payload(res) == {"available": True, "device": "NPU"}


def test_payload_returns_empty_on_malformed_text(es):
    res = _Result(text="not json {")
    assert es._payload(res) == {}


# --- known-server list (regression guard) ----------------------------------

def test_known_tuple_matches_summarizers(es):
    # Every KNOWN entry that has a summarizer is listed in SUMMARIZE; the
    # exception is edge-verify which uses initialize+list_tools instead.
    assert set(es.SUMMARIZE) <= set(es.KNOWN)
    assert "edge-verify" not in es.SUMMARIZE  # gate-only; no formatter
    assert "edge-verify" in es.KNOWN
