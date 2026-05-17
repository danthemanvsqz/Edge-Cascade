"""Mock-free regression net for orchestrator: the pure device->name mapping,
the trace/result dataclasses, and the real tee-logger wiring. The routing
decision tree needs live workers and is covered by the live smoke runs, not
by simulated mocks."""
import logging

from cascade.orchestrator import CascadeResult, Hop, Orchestrator, processor


def test_processor_maps_every_device():
    assert processor("NPU") == "Intel NPU (AI Boost)"
    assert processor("GPU.0") == "Intel iGPU (Xe)"
    assert processor("CPU") == "Intel CPU"
    assert processor("NVIDIA/qwen") == "NVIDIA RTX 5070 Ti (Ollama)"
    assert processor("claude-sonnet-4-6") == "Claude cloud (claude-sonnet-4-6)"


def test_trace_dataclasses():
    h = Hop("npu", "NPU", 1.5, "note")
    r = CascadeResult("ans", "npu", 2.0, [h])
    assert (h.tier, h.device, h.latency_s, h.note) == ("npu", "NPU", 1.5, "note")
    assert r.answer == "ans" and r.final_tier == "npu" and r.trace == [h]
    assert CascadeResult("a", "none", 0.0).trace == []   # default_factory


def test_build_logger_tees_to_file_and_stdout(tmp_path):
    path = tmp_path / "c.log"
    lg = Orchestrator._build_logger(path, verbose=True)
    assert lg.propagate is False
    kinds = sorted(type(h).__name__ for h in lg.handlers)
    assert kinds == ["FileHandler", "StreamHandler"]   # the tee
    lg.info("hello")
    assert "hello" in path.read_text(encoding="utf-8")

    quiet = Orchestrator._build_logger(path, verbose=False)
    assert [type(h).__name__ for h in quiet.handlers] == ["FileHandler"]
    assert logging.getLogger("cascade") is quiet       # named, reused
