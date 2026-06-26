"""Shape-contract tests for the Tier-2 GPU tasks (`generate_qwen14b`,
`generate_qwen7b`) — mirrors the pattern in test_tasks_npu.py.

Tasks.py is in coverage.omit so these are contract tests, not coverage tests.
Both happy-path and unavailable-path are covered; the key new assertion is that
`"seed"` appears in the result dict on success (SR-1 requirement).
"""
from __future__ import annotations

import pytest

pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import model_swap, tasks  # noqa: E402
from cascade.config import CONFIG  # noqa: E402
from cascade.gpu_worker import GPUResult  # noqa: E402
from cascade.llama_worker import LlamaResult  # noqa: E402


def _fake_gpu(mocker, *, text="```python\nreturn 1\n```", seed=42, available=True):
    """Patch the module-level `_gpu` singleton so no real Ollama/llama-cpp runs."""
    fake = mocker.Mock()
    fake.available.return_value = available
    fake.generate.return_value = GPUResult(
        text=text, latency_s=0.5, tokens_per_s=45.0,
        model="qwen2.5-coder:14b", seed=seed, available=True,
    )
    mocker.patch("cascade.tasks._gpu", fake)
    return fake


def test_generate_qwen14b_returns_canonical_shape(mocker):
    """generate_qwen14b() returns {available, text, model, tokens_per_s,
    latency_s, seed} matching the SR-1 contract."""
    _fake_gpu(mocker, seed=42)
    r = tasks.generate_qwen14b(prompt="write add(a, b)")
    assert r == {
        "available": True,
        "text": "```python\nreturn 1\n```",
        "model": "qwen2.5-coder:14b",
        "tokens_per_s": 45.0,
        "latency_s": 0.5,
        "seed": 42,
    }


def test_generate_qwen14b_unavailable_returns_handoff(mocker):
    """When the GPU tier is unreachable, returns the standard hand-off dict
    (available:false) with no seed key -- generation never ran."""
    _fake_gpu(mocker, available=False)
    r = tasks.generate_qwen14b(prompt="write add(a, b)")
    assert r == {
        "available": False,
        "model": CONFIG.gpu_model,
        "text": "[gpu tier unavailable -- Ollama not reachable]",
        "tokens_per_s": 0.0,
        "latency_s": 0.0,
    }
    assert "seed" not in r


def test_generate_qwen7b_returns_canonical_shape(mocker):
    """generate_qwen7b() returns the same shape as generate_qwen14b, including
    seed -- via the model_swap arbiter path (not the module-level _gpu)."""
    fake_worker = mocker.Mock()
    fake_worker.generate.return_value = LlamaResult(
        text="```python\nreturn 2\n```", latency_s=0.5, tokens_per_s=42.0,
        model="qwen2.5-coder:7b", seed=99, available=True,
    )
    model_swap._resident["qwen7b"] = model_swap.ModelHandle(
        name="qwen7b", footprint_mb=5500, handle=fake_worker)
    try:
        r = tasks.generate_qwen7b(prompt="write add(a, b)")
        assert r == {
            "available": True,
            "text": "```python\nreturn 2\n```",
            "model": "qwen2.5-coder:7b",
            "tokens_per_s": 42.0,
            "latency_s": 0.5,
            "seed": 99,
        }
    finally:
        del model_swap._resident["qwen7b"]
