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
    # `default_factory` (not a frozen class default) so Config() re-reads the
    # env per instantiation -- edge-cli.ps1 propagates CASCADE_NPU_MODEL_DIR
    # before launching Claude Code so launches from worktrees (whose models/
    # is empty by gitignore) still resolve against the main-tree models/.
    npu_model_dir: str = field(
        default_factory=lambda: os.environ.get(
            "CASCADE_NPU_MODEL_DIR",
            str(ROOT / "models" / "qwen2.5-coder-1.5b-npu"),
        )
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

    # Tier 2 — local GPU. Phase-2 Slice 1 introduced a SECOND backend:
    # llama-cpp-python loads the SAME GGUF weights Ollama caches, resident
    # in the worker process (no HTTP hop). `gpu_backend` picks which path
    # `cascade.gpu_worker.make_gpu_worker` returns; both produce the same
    # `GPUWorker` shape so callers and tests are unchanged.
    #
    # Slice 7 (2026-05-31): default flipped ollama→llama_cpp after PT-1/PT-2
    # confirmed full GPU offload and ±20% wall-time parity at flash_attn=True.
    # Override via CASCADE_GPU_BACKEND=ollama to revert.
    # See docs/FINDINGS-celery-phase2-parity.md (PT-2 decision gate).
    gpu_backend: str = os.environ.get("CASCADE_GPU_BACKEND", "llama_cpp")
    # GPU VRAM budget for the model.swap arbiter (Phase 2 Slice 3a). Defaults
    # to 12 GB (RTX 5070 Ti); override via CASCADE_VRAM_TOTAL_MB for boxes
    # with more/less. Per-model footprints in cascade/model_swap.py are
    # conservative (slightly overestimated) so the swap never OOMs the GPU.
    vram_total_mb: int = field(
        default_factory=lambda: int(os.environ.get("CASCADE_VRAM_TOTAL_MB", "12288"))
    )
    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    # On-disk root of Ollama's blob cache; the llama-cpp backend reads GGUFs
    # from here by resolving the Ollama manifest. Default matches the standard
    # Ollama layout (`~/.ollama/models`); override for non-default installs.
    ollama_models_dir: str = os.environ.get(
        "OLLAMA_MODELS",
        str(Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ollama" / "models"),
    )
    # Standard hard local. Deliberately the 14b coder, NOT the 7b: the 7b is
    # faster but fails the hard dijkstra-class gate 0/3 (FINDINGS §3) -- quality
    # over throughput. The 14b resolves it in one repair round.
    gpu_model: str = os.environ.get("CASCADE_GPU_MODEL", "qwen2.5-coder:14b")
    # Reasoning rung (FINDINGS §5): deepseek-r1:14b is the only local model that
    # solves the hard gate fresh (2/2, vs the coder's 0/3 fresh). DECLARED here
    # as the deliberate 3-rung GPU roster -- coder (gpu_model) for standard hard,
    # this for hardest reasoning, then PAID cloud. NOT yet wired into mesh.solve:
    # the gate-failure-triggered coder->r1 fallback before cloud is AI-3, gated
    # on the AI-4 complementarity experiment earning it. (The two 14b models
    # can't co-reside on 12GB, so AI-3 implies a model swap.)
    gpu_reasoning_model: str = os.environ.get(
        "CASCADE_GPU_REASONING_MODEL", "deepseek-r1:14b")

    # Tier "edge-image" (optional, C2) — SDXL via diffusers on the NVIDIA GPU,
    # served by scripts/image_server.py. Opt-in (`imagegen` extra). The agent
    # (Claude) mediates the prompt and critiques the result with its own vision.
    # SDXL (~8GB) and the 14b coder (~9GB) can't share 12GB VRAM -- image OR
    # code, not both (the Celery model-swap arbiter is the future fix).
    image_model: str = os.environ.get(
        "CASCADE_IMAGE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
    image_device: str = os.environ.get("CASCADE_IMAGE_DEVICE", "cuda")
    image_base_url: str = os.environ.get(
        "CASCADE_IMAGE_URL", "http://localhost:8188")
    image_artifacts_dir: str = os.environ.get(
        "CASCADE_IMAGE_ARTIFACTS", str(ROOT / "runs" / "artifacts"))
    image_steps: int = field(
        default_factory=lambda: int(os.environ.get("CASCADE_IMAGE_STEPS", "30")))
    image_guidance: float = field(
        default_factory=lambda: float(
            os.environ.get("CASCADE_IMAGE_GUIDANCE", "6.5")))
    image_size: int = field(
        default_factory=lambda: int(os.environ.get("CASCADE_IMAGE_SIZE", "1024")))

    # Tier 3 — cloud backstop (Anthropic). PAID. Off unless explicitly enabled
    # (Orchestrator(enable_cloud=True) / CLI --cloud / CASCADE_ENABLE_CLOUD=1),
    # AND a key is present. A key alone never enables the paid tier.
    cloud_model: str = os.environ.get("CASCADE_CLOUD_MODEL", "claude-opus-4-8")
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

    # PR review (sanctioned paid lane; the cascade build path stays $0). Reviews
    # go through the SAME credit guard + cost math and record to a SEPARATE
    # runs/edge-review.rec stream, so cascade spend ($0) is never conflated with
    # review spend. Tune via CASCADE_REVIEW_MODEL / _USD / _MAX_DIFF.
    review_model: str = os.environ.get(
        "CASCADE_REVIEW_MODEL", "claude-opus-4-8")
    review_usd_budget: float = field(
        default_factory=lambda: float(os.environ.get("CASCADE_REVIEW_USD", "0.50"))
    )
    review_max_diff_bytes: int = field(
        default_factory=lambda: int(
            os.environ.get("CASCADE_REVIEW_MAX_DIFF", "200000"))
    )
    # Dedicated review output ceiling (don't borrow the cloud-escalation cap).
    review_max_tokens: int = 4000
    # Cross-run review guards (cascade.review_ledger, SQLite). Beyond the per-call
    # credit guard: a per-PR ROUND cap and a DAILY USD budget, persisted in a
    # DURABLE local DB (no broker to be "down" -- a down Redis used to silently
    # disable these). The metered API is a fixed/prepaid spend, so the daily cap
    # is long-term budget HEALTH; if the DB can't be read the guards fail soft and
    # the per-call credit guard still bounds it. (Celery keeps its own
    # CASCADE_REDIS_URL in cascade/celery_app.py -- unrelated to this ledger.)
    review_max_rounds: int = field(
        default_factory=lambda: int(
            os.environ.get("CASCADE_REVIEW_MAX_ROUNDS", "3"))
    )
    review_daily_usd: float = field(
        default_factory=lambda: float(
            os.environ.get("CASCADE_REVIEW_DAILY_USD", "5.0"))
    )
    review_ledger_db: str = os.environ.get(
        "CASCADE_REVIEW_DB", str(ROOT / "runs" / "review-ledger.db"))

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
    gpu_temperature: float = field(
        default_factory=lambda: float(os.environ.get("CASCADE_GPU_TEMPERATURE", "0.8"))
    )
    gpu_top_p: float = field(
        default_factory=lambda: float(os.environ.get("CASCADE_GPU_TOP_P", "0.95"))
    )
    npu_temperature: float = field(
        default_factory=lambda: float(os.environ.get("CASCADE_NPU_TEMPERATURE", "0.0"))
    )
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
    # PD-1 v2 warn-prompt action lever. When True, cascade.mesh.solve threads
    # the prior draft's text-only degeneration reasons (looping / narrowing /
    # ...) into the next repair_prompt call so the repair model knows what
    # failure mode to avoid. REVERTed to default-off by the v2 A/B sweep
    # (docs/FINDINGS-pd1-v2-warn-prompt.md: P(trt>ctrl)=0.000, -4.4 pp pooled,
    # -6.2 pp on the only mid-regime subject). The wiring is preserved so the
    # experiment opt-in (scripts/warn_prompt_validation_v2.py) and any future
    # ablation can still drive it. Tune via CASCADE_WARN_PROMPT_ENABLED=1.
    warn_prompt_enabled: bool = field(
        default_factory=lambda: os.environ.get("CASCADE_WARN_PROMPT_ENABLED") == "1"
    )
    # PD-1 v2 skip-repair action lever. When True, cascade.mesh.solve DISCARDS
    # the poisoned NPU/iGPU draft when its observation scores >=
    # skip_repair_score_floor and routes the GPU phase into a fresh `generate`
    # instead of feeding the bad draft into the bounded repair loop. If the
    # fresh GPU generate also fails, the standard repair loop still runs --
    # but on the GPU's own output, never on the discarded NPU output. Distinct
    # from the hard-escalate lever (which would skip GPU entirely).
    # Default ON as of the v2 A/B sweep (docs/FINDINGS-pd1-v2-skip-repair.md:
    # P(trt>ctrl)=1.000, +22.8 pp pooled, +33.7 pp on the FIRED sub-pool, clean
    # null on subjects below the floor). The v2 calibration log shows 0
    # score>=0.30 hits on 27 correct-code negatives, so the trip rule is FP-free
    # at production. Roll back without a code change via
    # CASCADE_SKIP_REPAIR_ON_DEGEN=0.
    skip_repair_on_degen: bool = field(
        default_factory=lambda: os.environ.get(
            "CASCADE_SKIP_REPAIR_ON_DEGEN", "1") != "0"
    )
    skip_repair_score_floor: float = field(
        default_factory=lambda: float(
            os.environ.get("CASCADE_SKIP_REPAIR_SCORE_FLOOR", "0.30"))
    )
    # difficulty < this  -> Tier-1 (NPU/iGPU) draft handles it
    # [this, cloud)       -> Tier-2 (NVIDIA GPU)
    # >= cloud            -> Tier-3 (cloud), skipping the GPU
    # NPU-first: the verifier gate makes a wrong "easy" guess cheap (one ~3s NPU
    # draft, then escalate), so try the NPU for anything not flagged clearly
    # hard. The small router rates trivial code ~0.5-0.65; clearly-hard ~0.85+.
    escalate_to_gpu_difficulty: float = 0.70
    escalate_to_cloud_difficulty: float = 0.80
    # Length gate on the skip-draft rule (BACKLOG #8). The skip-draft optimization
    # (skip the NPU draft when the router flags a task hard) over-fires because
    # the small router OVER-RATES short input: a one-line "implement a red-black
    # tree" gets the same 0.85 as a 2000-char spec. So skip the cheap (~3s) NPU
    # draft only when the task is hard AND the prompt is at least this long;
    # shorter prompts always get the NPU shot (wins the over-rated-easy cases,
    # ~3s cost when it loses). 2026-05-30 log analysis: short over-rated skips
    # clustered ~66-133 chars vs genuine specs ~770-2344 -- a clean gap, so any
    # value in [160, 700] behaves identically on that data.
    skip_draft_min_chars: int = 240

    @property
    def cloud_enabled(self) -> bool:
        return self.enable_cloud and bool(self.anthropic_api_key)


CONFIG = Config()
