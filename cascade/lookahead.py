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
"""
from __future__ import annotations

import difflib
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cloud_worker import est_cost_usd, make_cloud_worker
from .config import CONFIG
from .gpu_worker import GPUWorker
from .npu_worker import NPUWorker
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

    @property
    def speedup_note(self) -> str:
        solo = sum(s.mode == "npu-solo" for s in self.steps)
        n = len(self.steps) or 1
        return (f"{solo}/{n} tasks answered NPU-solo "
                f"(GPU calls skipped: {solo})")


def _agreement(a: str, b: str) -> float:
    def norm(s: str) -> str:
        return " ".join(s.split())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def _logger() -> logging.Logger:
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
    return lg


class LookAhead:
    def __init__(self, accept_threshold: float = 0.55,
                 trust_window: int = 2, checkpoint_every: int = 3,
                 enable_cloud: bool = False) -> None:
        self.t = accept_threshold
        self.win = trust_window
        self.ckpt = checkpoint_every
        self.trust_left = 0
        self.since_ckpt = 0
        self.log = _logger()
        self.log.info("=== look-ahead started ===")
        self.npu = NPUWorker()
        self.gpu = GPUWorker()
        self.gpu_ok = self.gpu.available()
        self.cloud = make_cloud_worker(enabled=enable_cloud or CONFIG.enable_cloud)
        # Credit guard state (per LookAhead instance / run).
        self._cloud_calls = 0
        self._cloud_usd = 0.0
        self.log.info(f"NPU={self.npu.device} | GPU available={self.gpu_ok} | "
                      f"accept>={self.t} trust_window={self.win} "
                      f"checkpoint_every={self.ckpt}")
        self.log.info(
            f"cloud: {'ON' if self.cloud.enabled else 'OFF'} | credit guard: "
            f"<= {CONFIG.cloud_max_calls} calls and "
            f"<= ${CONFIG.cloud_usd_budget:.2f}/run"
        )

    def _cloud_blocked(self) -> str | None:
        """Return a reason string if the credit guard forbids a cloud call."""
        if not self.cloud.enabled:
            return "cloud disabled (gated off)"
        if self._cloud_calls >= CONFIG.cloud_max_calls:
            return f"call cap reached ({self._cloud_calls}/{CONFIG.cloud_max_calls})"
        if self._cloud_usd >= CONFIG.cloud_usd_budget:
            return (f"USD budget reached "
                    f"(${self._cloud_usd:.3f}/${CONFIG.cloud_usd_budget:.2f})")
        return None

    def step(self, task: str) -> Step:
        t0 = time.perf_counter()
        self.log.info(f"---- TASK: {task}")

        draft = self.npu.draft(task, max_new_tokens=CONFIG.npu_repair_max_tokens)
        self.log.info(f"  NPU drafted on {draft.device} ({draft.latency_s:.2f}s)")

        forced = self.since_ckpt >= self.ckpt
        if self.trust_left > 0 and not forced and self.gpu_ok:
            self.trust_left -= 1
            self.since_ckpt += 1
            answer, mode, who, agree = draft.text, "npu-solo", "npu", 1.0
            self.log.info(f"  TRUST: NPU-solo (GPU skipped), "
                          f"trust_left={self.trust_left}")
        else:
            if not self.gpu_ok:
                answer, mode, who, agree = draft.text, "npu-solo", "npu", 0.0
                self.log.info("  GPU unavailable -> NPU answer (unverified)")
            else:
                tag = "forced checkpoint" if forced else "verify"
                self.since_ckpt = 0
                prompt = (f"# TASK\n{task}\n\n# DRAFT ANSWER\n{draft.text}\n\n"
                          f"# INSTRUCTION\n{_VERIFY_SYS}")
                g = self.gpu.generate(prompt)
                agree = _agreement(draft.text, g.text)
                answer, mode, who = g.text, "verified", "gpu"
                self.trust_left = self.win if agree >= self.t else 0
                self.log.info(
                    f"  GPU {tag} on NVIDIA ({g.latency_s:.2f}s) | "
                    f"agreement={agree:.2f} -> "
                    f"{'TRUST granted' if agree >= self.t else 'no trust'} "
                    f"(trust_left={self.trust_left})")

        ok = verify(answer).passed
        if not ok:
            self.trust_left = 0  # a local miss -> don't trust NPU next round
            blocked = self._cloud_blocked()
            if blocked is None:
                self.log.info("  verifier FAIL -> escalating to CLOUD (paid)")
                c = self.cloud.generate(task, prior_attempt=answer)
                self._cloud_calls += 1
                self._cloud_usd += est_cost_usd(c)
                if c.available:
                    answer, who, mode = c.text, "cloud", "cloud-escalated"
                    ok = verify(answer).passed
                self.log.info(
                    f"  CLOUD {c.model} ({c.latency_s:.2f}s) "
                    f"~${est_cost_usd(c):.4f} | verifier="
                    f"{'PASS' if ok else 'FAIL'} | run total: "
                    f"{self._cloud_calls} call(s) ~${self._cloud_usd:.4f}")
            else:
                self.log.info(f"  verifier FAIL -> NO escalation: {blocked} "
                              f"(returning unverified local answer)")

        dt = time.perf_counter() - t0
        self.log.info(f"  => {who.upper()} answered | verifier={'PASS' if ok else 'FAIL'}"
                      f" | {dt:.2f}s")
        return Step(task, mode, who, agree, dt, ok, self.trust_left)

    def run(self, tasks: list[str]) -> LookAheadResult:
        res = LookAheadResult()
        for tk in tasks:
            res.steps.append(self.step(tk))
        self.log.info("---- summary: " + res.speedup_note)
        return res
