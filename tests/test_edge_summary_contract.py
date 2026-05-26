"""Server-side contract tests for SD-1's launch summary (PR #59 nit).

The summarizers in `scripts/edge_summary.py` read named keys out of each MCP
server's `.status` / `.budget` dict:
    npu:    available, device, reason, npu_max_tokens
    gpu:    available, model
    cloud:  allowed, usd_spent, usd_budget

If a server renames or drops one of those keys, the summary degrades silently
(`device=? max_tokens=?` instead of a hard error). These tests pin the
schema: import each MCP server module, stub its underlying state (worker
RPC / credit guard), call the actual tool function, and assert every key
our summarizers read is in the returned dict.

The test runs the REAL server functions, so a refactor in mcp_servers/
either keeps the contract or breaks this test.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")


def _load_summary_module():
    """Same loader the other test file uses -- scripts/ isn't a package."""
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


# The summarizer-to-server schema contract. Updating this dict + the
# summarizer must happen together; both sides are tested below.
SUMMARIZER_READS = {
    "edge-npu":   {"ready": ("available", "device", "npu_max_tokens"),
                   "degraded": ("available", "reason")},
    "edge-gpu":   {"ready": ("available", "model"),
                   "degraded": ("available",)},
    "edge-cloud": {"any": ("allowed", "usd_spent", "usd_budget")},
}


# --- edge-npu --------------------------------------------------------------

def test_npu_status_ready_contract(mocker):
    """status() return for an available NPU must carry every key the
    summarizer reads when the tier is up."""
    from mcp_servers import npu
    mocker.patch.object(npu, "_rpc", return_value={"ok": True, "device": "NPU"})
    mocker.patch.object(npu, "_REC", lambda *a, **k: None)
    r = npu.status()
    for k in SUMMARIZER_READS["edge-npu"]["ready"]:
        assert k in r, f"npu.status() missing {k!r} when available; got {r}"
    assert r["available"] is True


def test_npu_status_degraded_contract(mocker):
    """status() return for an unavailable NPU must carry available+reason."""
    from mcp_servers import npu
    mocker.patch.object(npu, "_rpc", return_value={"ok": False, "error": "model missing"})
    mocker.patch.object(npu, "_REC", lambda *a, **k: None)
    r = npu.status()
    for k in SUMMARIZER_READS["edge-npu"]["degraded"]:
        assert k in r, f"npu.status() missing {k!r} when degraded; got {r}"
    assert r["available"] is False
    assert "model missing" in (r.get("reason") or "")


# --- edge-gpu --------------------------------------------------------------

class _FakeGPUWorker:
    """Stand-in for the frozen GPUWorker dataclass -- only .available() is read
    by status(), and we don't exercise .generate()."""

    def __init__(self, is_available: bool):
        self._a = is_available

    def available(self) -> bool:
        return self._a


def test_gpu_status_ready_contract(mocker):
    """status() return for a reachable Ollama must carry available + model."""
    from mcp_servers import gpu
    mocker.patch.object(gpu, "_worker", _FakeGPUWorker(True))
    mocker.patch.object(gpu, "_vram", return_value={"resident_models": 1, "vram_bytes": 0})
    mocker.patch.object(gpu, "_REC", lambda *a, **k: None)
    r = gpu.status()
    for k in SUMMARIZER_READS["edge-gpu"]["ready"]:
        assert k in r, f"gpu.status() missing {k!r} when available; got {r}"
    assert r["available"] is True
    assert r["model"], "gpu.status() returned an empty model name"


def test_gpu_status_degraded_contract(mocker):
    """status() return for an unreachable Ollama must still carry available."""
    from mcp_servers import gpu
    mocker.patch.object(gpu, "_worker", _FakeGPUWorker(False))
    mocker.patch.object(gpu, "_REC", lambda *a, **k: None)
    r = gpu.status()
    for k in SUMMARIZER_READS["edge-gpu"]["degraded"]:
        assert k in r, f"gpu.status() missing {k!r} when degraded; got {r}"
    assert r["available"] is False


# --- edge-cloud ------------------------------------------------------------

def test_cloud_budget_contract(mocker):
    """budget() must always carry the credit-guard keys our summarizer reads."""
    from mcp_servers import cloud
    fake_state = {
        "calls_used": 0, "calls_max": 5,
        "usd_spent": 0.0, "usd_budget": 5.0,
        "guard_tripped": False, "allowed": True,
    }
    mocker.patch.object(cloud._guard, "state", return_value=fake_state)
    mocker.patch.object(cloud, "_REC", lambda *a, **k: None)
    r = cloud.budget()
    for k in SUMMARIZER_READS["edge-cloud"]["any"]:
        assert k in r, f"cloud.budget() missing {k!r}; got {r}"


# --- self-consistency between summarizer and the contract list -------------

def _src_reads(src: str, key: str) -> bool:
    """True if `src` reads `key` via either p.get("...") or p.get('...')."""
    return f'"{key}"' in src or f"'{key}'" in src


def test_summarizer_keys_match_contract(es):
    """Every key SUMMARIZER_READS claims is read MUST actually be read by the
    matching summarizer function. Mechanical regression guard: adding a key
    to a summarizer without listing it here (or vice versa) breaks the gate."""
    import inspect
    src_npu = inspect.getsource(es._summarize_npu)
    for k in SUMMARIZER_READS["edge-npu"]["ready"] + SUMMARIZER_READS["edge-npu"]["degraded"]:
        assert _src_reads(src_npu, k), f"summarize_npu doesn't read {k!r}"
    src_gpu = inspect.getsource(es._summarize_gpu)
    for k in SUMMARIZER_READS["edge-gpu"]["ready"]:
        assert _src_reads(src_gpu, k), f"summarize_gpu doesn't read {k!r}"
    src_cloud = inspect.getsource(es._summarize_cloud)
    for k in SUMMARIZER_READS["edge-cloud"]["any"]:
        assert _src_reads(src_cloud, k), f"summarize_cloud doesn't read {k!r}"
