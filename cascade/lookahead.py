"""Look-ahead speculation: synergize the NPU (fast drafter) and GPU (verifier).

True token-level speculative decoding needs the large model to expose logits /
a verify API. Ollama is text-in/text-out only, so that's off the table. The
achievable analog with the same components is *request-level* speculation:

  - the NPU speculatively answers the whole task (cheap, fast, lower quality);
  - a controller decides whether to trust it or have the GPU verify;
  - when the NPU keeps agreeing with the GPU, it earns a TRUST WINDOW and runs
    solo for the next few tasks (the actual speedup);
  - periodic forced GPU checkpoints bound drift even while trusted.

Agreement is measured (difflib ratio of NPU draft vs GPU's authoritative
answer); the final answer is gated by the existing code verifier.

There is no controller *object*. The state that genuinely evolves across the
task stream -- the trust window, the checkpoint counter, the credit-guard
accumulators -- lives in the frame of the `_iter_steps` GENERATOR (state
without an object; the natural Python idiom for per-stream iteration state).
The logger's lifetime is managed by the `_run_logger` context manager.
"""
from __future__ import annotations

import difflib
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .cloud_worker import est_cost_usd, make_cloud_worker
from .config import CONFIG
from .gpu_worker import make_gpu_worker
from .npu_worker import make_npu_worker
from .verifier import verify

_VERIFY_SYS = (
    "A fast draft answer is provided. Return the corrected, complete final "
    "answer for the task. Keep parts of the draft that are correct; fix the "
    "rest. Output a single ```python code block."
)


@dataclass
class Step:
    task: str
    mode: str          # "npu-solo" | "verified"
    answerer: str      # "npu" | "gpu"
    agreement: float   # NPU<->GPU similarity on verified steps (else carried)
    latency_s: float
    ok: bool           # final answer passed the code verifier
    trust_left: int


@dataclass
class LookAheadResult:
    steps: list[Step] = field(default_factory=list)


def speedup_note(result: LookAheadResult) -> str:
    """How many tasks the NPU answered solo. Was LookAheadResult.speedup_note
    -- derivation moved off the dataclass to a function."""
    solo = sum(s.mode == "npu-solo" for s in result.steps)
    n = len(result.steps) or 1
    return f"{solo}/{n} tasks answered NPU-solo (GPU calls skipped: {solo})"


def _agreement(a: str, b: str) -> float:
    def norm(s: str) -> str:
        return " ".join(s.split())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


@contextmanager
def _run_logger() -> Iterator[logging.Logger]:
    """Tee logger for a run; handlers are closed + detached on exit (the
    lifetime the old class left unmanaged)."""
    path = Path(CONFIG.log_path).parent / "lookahead.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("lookahead")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.handlers.clear()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    lg.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(ch)
    try:
        yield lg
    finally:
        for h in list(lg.handlers):
            h.close()
            lg.removeHandler(h)


def _cloud_blocked(cloud, calls: int, usd: float) -> str | None:
    """Reason string if the credit guard forbids a cloud call, else None.
    Pure -- derived only from its args."""
    if not cloud.enabled:
        return "cloud disabled (gated off)"
    if calls >= CONFIG.cloud_max_calls:
        return f"call cap reached ({calls}/{CONFIG.cloud_max_calls})"
    if usd >= CONFIG.cloud_usd_budget:
        return (f"USD budget reached "
                f"(${usd:.3f}/${CONFIG.cloud_usd_budget:.2f})")
    return None


def _iter_steps(
    tasks: list[str], *, npu, gpu, gpu_ok: bool, cloud,
    accept: float, win: int, ckpt: int, log: logging.Logger,
) -> Iterator[Step]:
    """Yield one Step per task. The controller state (trust window, checkpoint
    counter, credit-guard accumulators) is local to this generator's frame --
    it evolves across the stream without any object."""
    trust_left = 0
    since_ckpt = 0
    cloud_calls = 0
    cloud_usd = 0.0

    for task in tasks:
        t0 = time.perf_counter()
        log.info(f"---- TASK: {task}")

        draft = npu.draft(task, max_new_tokens=CONFIG.npu_repair_max_tokens)
        log.info(f"  NPU drafted on {draft.device} ({draft.latency_s:.2f}s)")

        forced = since_ckpt >= ckpt
        if trust_left > 0 and not forced and gpu_ok:
            trust_left -= 1
            since_ckpt += 1
            answer, mode, who, agree = draft.text, "npu-solo", "npu", 1.0
            log.info(f"  TRUST: NPU-solo (GPU skipped), "
                     f"trust_left={trust_left}")
        else:
            if not gpu_ok:
                answer, mode, who, agree = draft.text, "npu-solo", "npu", 0.0
                log.info("  GPU unavailable -> NPU answer (unverified)")
            else:
                tag = "forced checkpoint" if forced else "verify"
                since_ckpt = 0
                prompt = (f"# TASK\n{task}\n\n# DRAFT ANSWER\n{draft.text}\n\n"
                          f"# INSTRUCTION\n{_VERIFY_SYS}")
                g = gpu.generate(prompt)
                agree = _agreement(draft.text, g.text)
                answer, mode, who = g.text, "verified", "gpu"
                trust_left = win if agree >= accept else 0
                log.info(
                    f"  GPU {tag} on NVIDIA ({g.latency_s:.2f}s) | "
                    f"agreement={agree:.2f} -> "
                    f"{'TRUST granted' if agree >= accept else 'no trust'} "
                    f"(trust_left={trust_left})")

        ok = verify(answer).passed
        if not ok:
            trust_left = 0  # a local miss -> don't trust NPU next round
            blocked = _cloud_blocked(cloud, cloud_calls, cloud_usd)
            if blocked is None:
                log.info("  verifier FAIL -> escalating to CLOUD (paid)")
                c = cloud.generate(task, prior_attempt=answer)
                cloud_calls += 1
                cloud_usd += est_cost_usd(c)
                if c.available:
                    answer, who, mode = c.text, "cloud", "cloud-escalated"
                    ok = verify(answer).passed
                log.info(
                    f"  CLOUD {c.model} ({c.latency_s:.2f}s) "
                    f"~${est_cost_usd(c):.4f} | verifier="
                    f"{'PASS' if ok else 'FAIL'} | run total: "
                    f"{cloud_calls} call(s) ~${cloud_usd:.4f}")
            else:
                log.info(f"  verifier FAIL -> NO escalation: {blocked} "
                         f"(returning unverified local answer)")

        dt = time.perf_counter() - t0
        log.info(
            f"  => {who.upper()} answered | "
            f"verifier={'PASS' if ok else 'FAIL'} | {dt:.2f}s"
        )
        yield Step(task, mode, who, agree, dt, ok, trust_left)


def run_lookahead(
    tasks: list[str], *, accept_threshold: float = 0.55,
    trust_window: int = 2, checkpoint_every: int = 3,
    enable_cloud: bool = False,
) -> LookAheadResult:
    """Run the speculative look-ahead pipeline over `tasks`."""
    with _run_logger() as log:
        log.info("=== look-ahead started ===")
        npu = make_npu_worker()
        gpu = make_gpu_worker()
        gpu_ok = gpu.available()
        cloud = make_cloud_worker(
            enabled=enable_cloud or CONFIG.enable_cloud
        )
        log.info(f"NPU={npu.device} | GPU available={gpu_ok} | "
                 f"accept>={accept_threshold} trust_window={trust_window} "
                 f"checkpoint_every={checkpoint_every}")
        log.info(
            f"cloud: {'ON' if cloud.enabled else 'OFF'} | credit guard: "
            f"<= {CONFIG.cloud_max_calls} calls and "
            f"<= ${CONFIG.cloud_usd_budget:.2f}/run"
        )
        result = LookAheadResult(list(_iter_steps(
            tasks, npu=npu, gpu=gpu, gpu_ok=gpu_ok, cloud=cloud,
            accept=accept_threshold, win=trust_window,
            ckpt=checkpoint_every, log=log,
        )))
        log.info("---- summary: " + speedup_note(result))
        return result
