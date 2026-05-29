"""Tests for the direct-loading GPU backend -- Phase 2 Slice 1.

`cascade.llama_worker` itself is in `[tool.coverage.run] omit` (needs a real
GPU + GGUF to exercise the inference path), but the two surfaces that DON'T
need a GPU are pinned here:

- `_resolve_ollama_blob`: pure path/JSON manipulation; tested with a tmp
  Ollama-layout directory tree.
- The `_generate` / `make_llama_worker` contract: tested by mocking the
  `llama_cpp.Llama` class so no real model loads. Shape parity with
  `cascade.gpu_worker`'s `GPUWorker` -- the duck-typed contract callers
  rely on.

Also pins the `cascade.gpu_worker.make_gpu_worker` dispatch: with
`CASCADE_GPU_BACKEND=llama_cpp`, the Ollama path must NOT run (zero httpx
calls); with `=ollama` the existing path is untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cascade import llama_worker


def _build_ollama_tree(root: Path, model_id: str, body: bytes = b"GGUFv1...") -> Path:
    """Lay out a minimal Ollama-style cache: a manifest pointing at a blob
    digest and the blob itself. Returns the blob path so tests can assert
    `_resolve_ollama_blob` finds it."""
    name, tag = model_id.split(":", 1)
    digest = "sha256:abc123def456"
    sha = digest.replace(":", "-")
    manifests_dir = root / "manifests" / "registry.ollama.ai" / "library" / name
    manifests_dir.mkdir(parents=True)
    blobs_dir = root / "blobs"
    blobs_dir.mkdir()
    blob = blobs_dir / sha
    blob.write_bytes(body)
    (manifests_dir / tag).write_text(json.dumps({
        "schemaVersion": 2,
        "layers": [
            {
                "mediaType": "application/vnd.ollama.image.template",
                "digest": "sha256:0000",
                "size": 100,
            },
            {
                "mediaType": "application/vnd.ollama.image.model",
                "digest": digest,
                "size": len(body),
            },
        ],
    }), encoding="utf-8")
    return blob


def test_resolve_blob_finds_model_layer(tmp_path):
    """The manifest has multiple layers (template, model, license, ...);
    `_resolve_ollama_blob` must pick the one with
    `mediaType=application/vnd.ollama.image.model`."""
    expected = _build_ollama_tree(tmp_path, "qwen2.5-coder:14b")
    found = llama_worker._resolve_ollama_blob("qwen2.5-coder:14b", tmp_path)
    assert found == expected


def test_resolve_blob_defaults_to_latest_tag_when_no_colon(tmp_path):
    """An Ollama model id without `:tag` resolves to the `latest` manifest
    -- the same default Ollama applies."""
    expected = _build_ollama_tree(tmp_path, "qwen2.5-coder:latest")
    found = llama_worker._resolve_ollama_blob("qwen2.5-coder", tmp_path)
    assert found == expected


def test_resolve_blob_raises_clear_error_when_manifest_missing(tmp_path):
    """Model not pulled => RuntimeError with a clear `ollama pull` hint."""
    (tmp_path / "blobs").mkdir()
    with pytest.raises(RuntimeError, match=r"manifest not found.*ollama pull"):
        llama_worker._resolve_ollama_blob("not-a-real-model:14b", tmp_path)


def test_resolve_blob_raises_when_layer_blob_missing(tmp_path):
    """Manifest references a digest whose blob isn't on disk -- corrupted
    cache. RuntimeError pointing at the missing file."""
    name, tag = "qwen2.5-coder", "14b"
    digest = "sha256:abc123def456"
    manifests_dir = tmp_path / "manifests" / "registry.ollama.ai" / "library" / name
    manifests_dir.mkdir(parents=True)
    (tmp_path / "blobs").mkdir()
    (manifests_dir / tag).write_text(json.dumps({
        "layers": [{
            "mediaType": "application/vnd.ollama.image.model",
            "digest": digest, "size": 1,
        }],
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"layer.*not present"):
        llama_worker._resolve_ollama_blob("qwen2.5-coder:14b", tmp_path)


def test_resolve_blob_raises_when_no_model_layer(tmp_path):
    """A manifest with no `image.model` layer is malformed; clear error."""
    name, tag = "qwen2.5-coder", "14b"
    manifests_dir = tmp_path / "manifests" / "registry.ollama.ai" / "library" / name
    manifests_dir.mkdir(parents=True)
    (manifests_dir / tag).write_text(json.dumps({
        "layers": [{
            "mediaType": "application/vnd.ollama.image.template",
            "digest": "sha256:abc", "size": 1,
        }],
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"no.*image.model.*layer"):
        llama_worker._resolve_ollama_blob("qwen2.5-coder:14b", tmp_path)


def test_generate_returns_llama_result_shape(mocker):
    """The inference path -- mocked at the `Llama` boundary -- returns a
    `LlamaResult` with text, latency, tokens/s, model id, available=True.
    Duck-types `GPUResult` so callers expecting either work."""
    fake_llm = mocker.Mock()
    fake_llm.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "```python\nprint('hi')\n```"}}],
        "usage": {"completion_tokens": 7},
    }
    out = llama_worker._generate(fake_llm, "qwen2.5-coder:14b", "say hi")
    assert isinstance(out, llama_worker.LlamaResult)
    assert out.text == "```python\nprint('hi')\n```"
    assert out.model == "qwen2.5-coder:14b"
    assert out.available is True
    assert out.latency_s >= 0
    assert out.tokens_per_s >= 0  # 7 / latency, but latency could round to 0


def test_generate_passes_max_tokens_through(mocker):
    """`max_new_tokens` becomes `max_tokens` in the llama-cpp chat-completion
    call. Pinned so a refactor doesn't silently drop the limit."""
    fake_llm = mocker.Mock()
    fake_llm.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"completion_tokens": 1},
    }
    llama_worker._generate(fake_llm, "model", "q", max_new_tokens=64)
    call = fake_llm.create_chat_completion.call_args
    assert call.kwargs["max_tokens"] == 64


def test_generate_hands_off_when_llama_raises(mocker):
    """An exception from llama-cpp => `available:false` with the error
    message in `text`. NEVER raises upward -- the cascade treats a broken
    inference call as a status, not an error (charter inv. 5)."""
    fake_llm = mocker.Mock()
    fake_llm.create_chat_completion.side_effect = RuntimeError("oom")
    out = llama_worker._generate(fake_llm, "model", "q")
    assert out.available is False
    assert "oom" in out.text


def test_make_llama_worker_returns_gpuworker_shape(tmp_path, mocker):
    """The full builder: resolve blob -> load Llama -> bind closures.
    Returns a `GPUWorker` duck-typed equivalent to
    `cascade.gpu_worker.GPUWorker` (model + available + generate)."""
    _build_ollama_tree(tmp_path, "qwen2.5-coder:14b")
    # Stub the Llama class so no real model loads.
    fake_llama_cls = mocker.Mock()
    fake_llama_cls.return_value = mocker.Mock()
    fake_module = mocker.Mock()
    fake_module.Llama = fake_llama_cls
    mocker.patch("cascade.llama_worker._llama", return_value=fake_module)
    mocker.patch("cascade.llama_worker.CONFIG", mocker.Mock(
        ollama_models_dir=str(tmp_path),
        gpu_model="qwen2.5-coder:14b",
        gpu_max_new_tokens=1024,
    ))
    worker = llama_worker.make_llama_worker()
    assert worker.model == "qwen2.5-coder:14b"
    assert worker.available() is True
    assert callable(worker.generate)
    # Llama was constructed with the resolved blob path + n_gpu_layers=-1
    # (offload all to GPU) + a sane context window.
    fake_llama_cls.assert_called_once()
    kwargs = fake_llama_cls.call_args.kwargs
    assert kwargs["n_gpu_layers"] == -1
    assert kwargs["n_ctx"] >= 4096


def test_make_llama_worker_uses_explicit_model_id(tmp_path, mocker):
    """Caller can override the model id (e.g. `qwen2.5-coder:7b`); the
    `_resolve_ollama_blob` lookup keys on the supplied id, not on CONFIG."""
    _build_ollama_tree(tmp_path, "qwen2.5-coder:7b")
    fake_llama_cls = mocker.Mock()
    fake_module = mocker.Mock()
    fake_module.Llama = fake_llama_cls
    mocker.patch("cascade.llama_worker._llama", return_value=fake_module)
    mocker.patch("cascade.llama_worker.CONFIG", mocker.Mock(
        ollama_models_dir=str(tmp_path),
        gpu_max_new_tokens=1024,
    ))
    worker = llama_worker.make_llama_worker("qwen2.5-coder:7b")
    assert worker.model == "qwen2.5-coder:7b"


def test_gpu_worker_dispatch_picks_llama_cpp_backend(mocker):
    """`cascade.gpu_worker.make_gpu_worker` returns the llama_cpp worker
    when `CASCADE_GPU_BACKEND=llama_cpp`. Pinned so a config refactor
    doesn't silently keep the Ollama path engaged."""
    from cascade import gpu_worker
    mocker.patch("cascade.gpu_worker.CONFIG", mocker.Mock(
        gpu_backend="llama_cpp"))
    sentinel = mocker.Mock()
    fake = mocker.patch("cascade.llama_worker.make_llama_worker",
                        return_value=sentinel)
    out = gpu_worker.make_gpu_worker()
    assert out is sentinel
    fake.assert_called_once()


def test_gpu_worker_dispatch_picks_ollama_backend_by_default(mocker):
    """Default `CASCADE_GPU_BACKEND=ollama` keeps the Ollama HTTP path.
    No call to llama_worker.make_llama_worker; the returned GPUWorker
    binds the Ollama `_available`/`_generate` closures."""
    from cascade import gpu_worker
    mocker.patch("cascade.gpu_worker.CONFIG", mocker.Mock(
        gpu_backend="ollama",
        ollama_base_url="http://localhost:11434",
        gpu_model="qwen2.5-coder:14b",
    ))
    fake_llama = mocker.patch("cascade.llama_worker.make_llama_worker")
    out = gpu_worker.make_gpu_worker()
    assert isinstance(out, gpu_worker.GPUWorker)
    assert out.model == "qwen2.5-coder:14b"
    fake_llama.assert_not_called()


def test_gpu_worker_dispatch_rejects_unknown_backend(mocker):
    """An unrecognised `CASCADE_GPU_BACKEND` value is a config typo, not a
    silent fall-through. Loud failure beats a phantom Ollama call."""
    from cascade import gpu_worker
    mocker.patch("cascade.gpu_worker.CONFIG", mocker.Mock(
        gpu_backend="not-a-backend"))
    with pytest.raises(ValueError, match=r"unknown CASCADE_GPU_BACKEND"):
        gpu_worker.make_gpu_worker()
