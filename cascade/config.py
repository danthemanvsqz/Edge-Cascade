import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional dependency fallback
    pass

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    # Tier 1 — NPU (OpenVINO GenAI). Device fallback order if NPU compile fails.
    # NPU-compatible export: symmetric, channel-wise INT4 (--sym --group-size=-1).
    # The default asymmetric/group-quantized int4-ov export crashes the vpux
    # compiler; this one compiles on the NPU.
    npu_model_dir: str = os.environ.get(
        "CASCADE_NPU_MODEL_DIR", str(ROOT / "models" / "qwen2.5-coder-1.5b-npu")
    )
    # Set CASCADE_SKIP_NPU=1 to skip the NPU probe entirely (it crashes on
    # models the vpux compiler can't digest; iGPU is the reliable Tier-1 path).
    npu_device_order: tuple[str, ...] = field(
        default_factory=lambda: (
            ("GPU.0", "CPU")
            if os.environ.get("CASCADE_SKIP_NPU") == "1"
            else ("NPU", "GPU.0", "CPU")
        )
    )

    # Tier 1b (optional) — a larger draft model on the Intel iGPU (Xe) via
    # OpenVINO. Idle by default: set CASCADE_IGPU_MODEL_DIR to a 3B-class INT4
    # export to enable a stronger draft than the 1.5B NPU (the 1.5B fails the
    # dijkstra-class gate 0/9; see PLAN C3 spike). When unset, the iGPU
    # drafter is not built and topologies that name it fall back to the NPU.
    igpu_model_dir: str = os.environ.get("CASCADE_IGPU_MODEL_DIR", "")
    igpu_device: str = os.environ.get("CASCADE_IGPU_DEVICE", "GPU.0")
    igpu_max_new_tokens: int = 768

    # Tier 2 — local GPU via Ollama (NVIDIA/CUDA).
    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    gpu_model: str = os.environ.get("CASCADE_GPU_MODEL", "qwen2.5-coder:14b")

    # Tier 3 — cloud backstop (Anthropic). PAID. Off unless explicitly enabled
    # (Orchestrator(enable_cloud=True) / CLI --cloud / CASCADE_ENABLE_CLOUD=1),
    # AND a key is present. A key alone never enables the paid tier.
    cloud_model: str = os.environ.get("CASCADE_CLOUD_MODEL", "claude-sonnet-4-6")
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY") or None
    )
    enable_cloud: bool = field(
        default_factory=lambda: os.environ.get("CASCADE_ENABLE_CLOUD") == "1"
    )
    # Credit guard: hard ceilings on the PAID tier per pipeline run. Cloud is
    # refused once EITHER is reached (deterministic call cap + conservative
    # USD estimate). Tune via CASCADE_CLOUD_MAX_CALLS / CASCADE_CLOUD_USD.
    cloud_max_calls: int = field(
        default_factory=lambda: int(os.environ.get("CASCADE_CLOUD_MAX_CALLS", "3"))
    )
    cloud_usd_budget: float = field(
        default_factory=lambda: float(os.environ.get("CASCADE_CLOUD_USD", "0.50"))
    )

    # Escalation gate thresholds.
    # Live log file — tail -f this while driving the CLI.
    log_path: str = os.environ.get(
        "CASCADE_LOG", str(ROOT / "runs" / "cascade.log")
    )

    npu_max_new_tokens: int = 192
    # Repair needs room for a whole corrected block; the NPU's static-shape
    # prompt limit still caps how large an input it can repair.
    npu_repair_max_tokens: int = 640
    gpu_max_new_tokens: int = 1024
    cloud_max_tokens: int = 16000

    # Repair-prompt budget (offline validate_log repair loop). The prompt is
    # sliced to the implicated symbols and the failures list deduped/capped so
    # a deep synthesis run can't blow the local model's context -- decisive for
    # the npu_repair_max_tokens=640 cap above, where a whole-program prompt
    # never fits. Tune via CASCADE_REPAIR_MAX_FAILURES / _OBSERVED_MAXLEN.
    repair_max_failures: int = field(
        default_factory=lambda: int(
            os.environ.get("CASCADE_REPAIR_MAX_FAILURES", "6")
        )
    )
    repair_observed_maxlen: int = field(
        default_factory=lambda: int(
            os.environ.get("CASCADE_REPAIR_OBSERVED_MAXLEN", "600")
        )
    )
    # Deterministic repair-ROUND cap for the live cascade (distinct from
    # repair_max_failures, which bounds failures-per-prompt). After this many
    # GPU repair rounds without a passing gate, the mesh stops and hands off to
    # Tier-3; a further round is a policy breach (over_cap_episodes). SINGLE
    # SOURCE OF TRUTH -- cascade/topologies.py and dashboard.py both read this
    # (Celery-readiness charter, invariant 4). Tune via CASCADE_REPAIR_CAP.
    repair_cap: int = field(
        default_factory=lambda: int(os.environ.get("CASCADE_REPAIR_CAP", "2"))
    )
    # difficulty < this  -> Tier-1 (NPU/iGPU) draft handles it
    # [this, cloud)       -> Tier-2 (NVIDIA GPU)
    # >= cloud            -> Tier-3 (cloud), skipping the GPU
    # NPU-first: the verifier gate makes a wrong "easy" guess cheap (one ~3s NPU
    # draft, then escalate), so try the NPU for anything not flagged clearly
    # hard. The small router rates trivial code ~0.5-0.65; clearly-hard ~0.85+.
    escalate_to_gpu_difficulty: float = 0.70
    escalate_to_cloud_difficulty: float = 0.80

    @property
    def cloud_enabled(self) -> bool:
        return self.enable_cloud and bool(self.anthropic_api_key)


CONFIG = Config()
