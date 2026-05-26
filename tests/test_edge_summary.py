"""Tests for scripts/edge_summary.py (SD-1).

Covers the pure formatters, _params, the mocked _query dispatch (every tier's
branch + timeout + exception), and build_rows/format_rows. The real _query
path speaks MCP stdio against a subprocess -- that's the launcher's live
smoke domain (edge-cli.ps1 -Check), not the unit suite's. We mock stdio_client
and ClientSession to exercise the dispatch logic deterministically.

The launcher-glue main() / __main__ block is pragma-no-cover per project
convention -- main is a launcher, not logic.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")  # script imports mcp at top; skip if extra absent


def _load_summary_module():
    """Load scripts/edge_summary.py as a module despite living outside a
    package. Done lazily so the importorskip above runs first."""
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


# --- async-mock helpers ----------------------------------------------------
# stdio_client is an @asynccontextmanager returning a (reader, writer) pair.
# ClientSession is a class used as an async context manager. We replace both
# at the module's globals so _query exercises the dispatch deterministically.

class _AsyncCM:
    """Minimal async context manager that yields a configured value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _FakeTools:
    def __init__(self, n):
        self.tools = list(range(n))


class _FakeSession:
    """Stand-in for mcp.ClientSession. Configure via class attrs before use:
        _FakeSession.init_raises = exc_or_None
        _FakeSession.list_tools_n = int
        _FakeSession.call_tool_payload = dict or "TIMEOUT" or exc instance
    """

    init_raises = None
    list_tools_n = 3
    call_tool_payload = None
    initialized = False
    list_tools_called = False
    last_tool_call: tuple[str, dict] | None = None

    def __init__(self, r, w):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        type(self).initialized = True
        if type(self).init_raises is not None:
            raise type(self).init_raises

    async def list_tools(self):
        type(self).list_tools_called = True
        return _FakeTools(type(self).list_tools_n)

    async def call_tool(self, name, args):
        type(self).last_tool_call = (name, args)
        p = type(self).call_tool_payload
        if isinstance(p, BaseException):
            raise p
        if p == "TIMEOUT":
            raise TimeoutError("simulated")
        # Build a MCP-result-like object with structuredContent.
        return _Result(structured={"result": p or {}})


class _Result:
    """Minimal stand-in for the MCP CallToolResult shape we read. Reused by
    the _payload tests below and by _FakeSession.call_tool's return."""

    def __init__(self, structured=None, text=None):
        self.structuredContent = structured
        self.content = [_Text(text)] if text is not None else []


class _Text:
    def __init__(self, t):
        self.text = t


@pytest.fixture(autouse=True)
def _reset_fake_session():
    """Reset the class-level config between tests so they don't leak."""
    _FakeSession.init_raises = None
    _FakeSession.list_tools_n = 3
    _FakeSession.call_tool_payload = None
    _FakeSession.initialized = False
    _FakeSession.list_tools_called = False
    _FakeSession.last_tool_call = None
    yield


@pytest.fixture
def patched_mcp(es, mocker):
    """Patch stdio_client + ClientSession on the edge_summary module."""
    fake_stdio = lambda params, errlog=None: _AsyncCM((None, None))  # noqa: E731
    mocker.patch.object(es, "stdio_client", fake_stdio)
    mocker.patch.object(es, "ClientSession", _FakeSession)


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
# _Result / _Text live in the async-mock helpers section above; reused here.

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


# --- _params ---------------------------------------------------------------

def test_params_with_full_spec(es):
    spec = {"command": "py", "args": ["-m", "x"], "env": {"K": "v"}, "cwd": "."}
    p = es._params(spec)
    assert p.command == "py"
    assert p.args == ["-m", "x"]
    assert p.env == {"K": "v"}
    assert p.cwd == "."


def test_params_empty_env_becomes_none(es):
    """An absent/empty env passes None so MCP inherits the parent process."""
    p = es._params({"command": "py", "args": ["-m", "x"]})
    assert p.env is None
    p2 = es._params({"command": "py", "args": [], "env": {}})
    assert p2.env is None  # falsy env -> None


# --- _query (mocked stdio) -------------------------------------------------
# These call asyncio.run() inside sync tests so we don't need a separate
# async test runner (pytest-asyncio / pytest-anyio). The mocked stdio_client
# returns immediately so each test is sub-millisecond.

def _run(coro):
    return asyncio.run(coro)


def test_query_verify_path_uses_list_tools(es, patched_mcp):
    """edge-verify has no .status tool; readiness = successful list_tools()."""
    _FakeSession.list_tools_n = 4
    state, summary = _run(es._query("edge-verify", {"command": "py", "args": []}))
    assert state == "READY"
    assert "deterministic sandbox" in summary
    assert "4 tools" in summary
    assert _FakeSession.list_tools_called is True


def test_query_npu_ready(es, patched_mcp):
    _FakeSession.call_tool_payload = {"available": True, "device": "NPU", "npu_max_tokens": 320}
    state, summary = _run(es._query("edge-npu", {"command": "py", "args": []}))
    assert state == "READY"
    assert "NPU" in summary and "320" in summary
    assert _FakeSession.last_tool_call == ("status", {})


def test_query_gpu_degraded(es, patched_mcp):
    _FakeSession.call_tool_payload = {"available": False}
    state, summary = _run(es._query("edge-gpu", {"command": "py", "args": []}))
    assert state == "DEGRADED"
    assert "Ollama" in summary


def test_query_cloud_uses_budget_tool(es, patched_mcp):
    _FakeSession.call_tool_payload = {"allowed": True, "usd_spent": 0.1, "usd_budget": 5.0}
    state, _ = _run(es._query("edge-cloud", {"command": "py", "args": []}))
    assert state == "READY"
    assert _FakeSession.last_tool_call == ("budget", {})


def test_query_unknown_server_generic_render(es, patched_mcp):
    """A server not in SUMMARIZE renders the raw payload generically."""
    _FakeSession.call_tool_payload = {"available": True, "custom": "x"}
    state, summary = _run(es._query("edge-future", {"command": "py", "args": []}))
    assert state == "READY"
    assert "custom" in summary


def test_query_unknown_server_degraded_when_unavailable(es, patched_mcp):
    _FakeSession.call_tool_payload = {"available": False}
    state, _ = _run(es._query("edge-future", {"command": "py", "args": []}))
    assert state == "DEGRADED"


def test_query_timeout_becomes_degraded(es, patched_mcp):
    _FakeSession.call_tool_payload = "TIMEOUT"
    state, summary = _run(es._query("edge-npu", {"command": "py", "args": []}))
    assert state == "DEGRADED"
    assert "timeout" in summary.lower()


def test_query_exception_becomes_error(es, patched_mcp):
    _FakeSession.init_raises = RuntimeError("boom")
    state, summary = _run(es._query("edge-npu", {"command": "py", "args": []}))
    assert state == "ERROR"
    assert "RuntimeError" in summary and "boom" in summary


# --- build_rows ------------------------------------------------------------

def test_build_rows_keeps_known_order_and_marks_unwired(es, patched_mcp):
    _FakeSession.call_tool_payload = {"available": True, "device": "NPU", "npu_max_tokens": 320}
    rows = _run(es.build_rows({"edge-npu": {"command": "py", "args": []}}))
    names = [r[0] for r in rows]
    assert names == ["edge-npu", "edge-gpu", "edge-verify", "edge-cloud"]
    # First row is queried (READY); the rest are NOT WIRED.
    assert rows[0][1] == "READY"
    for r in rows[1:]:
        assert r[1] == "NOT WIRED"


def test_build_rows_cloud_unwired_hint(es, patched_mcp):
    """The cloud-tier NOT WIRED note pitches the -WithCloud opt-in."""
    rows = _run(es.build_rows({}))
    cloud = next(r for r in rows if r[0] == "edge-cloud")
    assert "-WithCloud" in cloud[2]
    other = next(r for r in rows if r[0] == "edge-npu")
    assert "not in this config" in other[2]


def test_build_rows_includes_extras_after_known(es, patched_mcp):
    _FakeSession.call_tool_payload = {"available": True, "device": "?"}
    rows = _run(es.build_rows({"edge-future": {"command": "py", "args": []}}))
    names = [r[0] for r in rows]
    # KNOWN first (all NOT WIRED), then the extras at the end.
    assert names[-1] == "edge-future"
    assert names[:4] == list(es.KNOWN)


# --- format_rows -----------------------------------------------------------

def test_format_rows_aligns_columns(es):
    rows = [
        ("a",    "READY",    "x"),
        ("bbbb", "DEGRADED", "y"),
        ("cc",   "NOT WIRED", "z"),
    ]
    lines = es.format_rows(rows)
    assert len(lines) == 3
    # Every line starts with the 4-space indent.
    assert all(line.startswith("    ") for line in lines)
    # The bracket-column position is identical across rows -- pick a
    # representative substring and confirm it lands at the same column on each
    # row by anchoring to the longest bracketed state.
    bracket_starts = [line.index("[") for line in lines]
    assert len(set(bracket_starts)) == 1
    summary_starts = [line.rindex("  ") for line in lines]
    assert len(set(summary_starts)) == 1


def test_format_rows_handles_single_row(es):
    lines = es.format_rows([("only", "READY", "msg")])
    assert lines == ["    only  [READY]  msg"]


def test_format_rows_empty_input_returns_empty_list(es):
    """A safety net so a future refactor that lets build_rows return [] can't
    crash the launcher via max() on an empty sequence."""
    assert es.format_rows([]) == []
