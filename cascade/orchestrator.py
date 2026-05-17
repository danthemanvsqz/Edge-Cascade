"""3-tier escalation cascade: NPU/iGPU -> NVIDIA GPU -> Claude cloud.

Routing: the Tier-1 model scores difficulty up front, so trivial asks never
spin up the GPU and known-hard asks skip straight to cloud. Every local answer
is gated by the code verifier; a failed gate escalates and the failed draft is
handed to the next tier as context.

A live log prints which physical processor is working at each phase so you can
watch the cascade hop across hardware in real time.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cloud_worker import CloudWorker
from .config import CONFIG
from .gpu_worker import GPUWorker
from .logfmt import dump_record
from .npu_worker import NPUWorker
from .verifier import verify


def processor(device: str) -> str:
    """Map an internal device string to a human-readable processor name."""
    if device == "NPU":
        return "Intel NPU (AI Boost)"
    if device == "GPU.0":
        return "Intel iGPU (Xe)"
    if device == "CPU":
        return "Intel CPU"
    if device.startswith("NVIDIA/"):
        return "NVIDIA RTX 5070 Ti (Ollama)"
    return f"Claude cloud ({device})"  # device == cloud model id


@dataclass
class Hop:
    tier: str
    device: str
    latency_s: float
    note: str


@dataclass
class CascadeResult:
    answer: str
    final_tier: str
    total_latency_s: float
    trace: list[Hop] = field(default_factory=list)


class Orchestrator:
    def __init__(self, verbose: bool = True, enable_cloud: bool = False) -> None:
        self.verbose = verbose
        self.log_path = Path(CONFIG.log_path)
        # Deterministically-parseable sibling stream (see cascade/logfmt.py).
        # The .log tee is for humans; this .rec is what validate_log consumes.
        self.rec_path = self.log_path.with_suffix(".rec")
        self._seq = 0
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = self._build_logger(self.log_path, verbose)
        self._logger.info("=== cascade started ===")
        self.npu = NPUWorker()
        self.gpu = GPUWorker()
        # Paid tier: enabled only via this parameter or CASCADE_ENABLE_CLOUD=1.
        self.cloud = CloudWorker(enabled=enable_cloud or CONFIG.enable_cloud)
        self._logger.info(
            f"config: Tier-1={processor(self.npu.device)} | "
            f"Tier-2=NVIDIA RTX 5070 Ti | Tier-3={self.cloud.status()}"
        )

    @staticmethod
    def _build_logger(path: Path, verbose: bool) -> logging.Logger:
        # One logger, two handlers = tee: every record fans out to the file
        # (with wall-clock timestamp, for `tail -f`) and, if verbose, stdout.
        logger = logging.getLogger("cascade")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()  # avoid duplicate handlers on re-init
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        logger.addHandler(fh)
        if verbose:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(ch)
        return logger

    def _log(self, t0: float, msg: str) -> None:
        self._logger.info(f"  [{time.perf_counter() - t0:6.2f}s] {msg}")

    def _start(self, t0: float, tier: str, device: str) -> None:
        self._log(t0, f">> {tier:<7} working on  {processor(device)}")

    def _done(self, t0: float, tier: str, note: str, dt: float) -> None:
        self._log(t0, f"<< {tier:<7} {note}  ({dt:.2f}s)")

    def _gpu_then_cloud(
        self, query: str, trace: list[Hop], t0: float,
        fallback_text: str | None = None,
    ) -> CascadeResult:
        prior = fallback_text
        if self.gpu.available():
            self._start(t0, "GPU", "NVIDIA/")
            g = self.gpu.generate(query)
            v = verify(g.text)
            note = f"{g.tokens_per_s:.0f} tok/s; gate: {v.reason}"
            self._done(t0, "GPU", note, g.latency_s)
            trace.append(Hop("gpu", f"NVIDIA/{g.model}", g.latency_s, note))
            if g.available and v.passed:
                return CascadeResult(
                    g.text, "gpu", time.perf_counter() - t0, trace
                )
            if g.available:
                prior = g.text
        else:
            self._log(t0, "-- GPU     unavailable (Ollama not reachable) - skipped")
            trace.append(Hop("gpu", "NVIDIA/Ollama", 0.0, "unavailable - skipped"))

        if not self.cloud.enabled:
            self._log(t0, "-- CLOUD   paid tier disabled - returning best local answer")
            if prior is not None:
                trace.append(Hop("local", "best-effort", 0.0,
                                  "unverified; cloud disabled"))
                return CascadeResult(
                    prior, "local (unverified)",
                    time.perf_counter() - t0, trace,
                )
            msg = ("[no local answer passed the gate and the paid cloud tier "
                   "is disabled. Re-run with --cloud to escalate.]")
            trace.append(Hop("local", "none", 0.0, "no answer; cloud disabled"))
            return CascadeResult(msg, "none", time.perf_counter() - t0, trace)

        self._start(t0, "CLOUD", self.cloud._model)
        c = self.cloud.generate(query, prior_attempt=prior)
        self._done(t0, "CLOUD", c.reason_note(), c.latency_s)
        trace.append(Hop("cloud", c.model, c.latency_s, c.reason_note()))
        return CascadeResult(c.text, "cloud", time.perf_counter() - t0, trace)

    def _write_record(self, query: str, result: CascadeResult) -> None:
        """Append one deterministic record (cascade/logfmt.py grammar). The
        free-text query/answer are length-framed, so a model answer that emits
        fake "%%REC"/timestamp lines can never corrupt the parse."""
        trace = "\n".join(
            f"{h.tier}|{h.device}|{h.latency_s:.2f}s|{h.note}"
            for h in result.trace
        )
        rec = dump_record(self._seq, {
            "query": query,
            "answer": result.answer,
            "final_tier": result.final_tier,
            "total_latency_s": f"{result.total_latency_s:.2f}",
            "trace": trace,
        })
        with open(self.rec_path, "a", encoding="utf-8") as fh:
            fh.write(rec)
        self._seq += 1

    def run(self, query: str) -> CascadeResult:
        """Run the cascade and log the full outcome (trace, summary, answer)."""
        result = self._run(query)
        self._write_record(query, result)
        self._logger.info("---- trace ----")
        for h in result.trace:
            self._logger.info(
                f"  [{h.tier:<6}] {h.device:<22} "
                f"{h.latency_s:6.2f}s  {h.note}"
            )
        self._logger.info(
            f"== ANSWERED BY {result.final_tier.upper()} "
            f"in {result.total_latency_s:.2f}s =="
        )
        self._logger.info("ANSWER:\n" + result.answer + "\n")
        return result

    def _run(self, query: str) -> CascadeResult:
        t0 = time.perf_counter()
        trace: list[Hop] = []
        # Log the full query (single-lined) -- log-driven repair needs the
        # complete task, not a preview.
        self._logger.info(
            "---- QUERY: " + " ".join(query.split())[:2000]
        )

        self._start(t0, "ROUTER", self.npu.device)
        r = self.npu.route(query)
        self._done(
            t0, "ROUTER",
            f"difficulty={r.difficulty:.2f} category={r.category}", r.latency_s,
        )
        trace.append(
            Hop("router", r.device, r.latency_s,
                f"difficulty={r.difficulty:.2f} category={r.category}")
        )

        if r.difficulty >= CONFIG.escalate_to_cloud_difficulty:
            if self.cloud.enabled:
                self._log(t0, "   route: clearly hard -> straight to CLOUD")
                self._start(t0, "CLOUD", self.cloud._model)
                c = self.cloud.generate(query)
                self._done(t0, "CLOUD", c.reason_note(), c.latency_s)
                trace.append(Hop("cloud", c.model, c.latency_s, c.reason_note()))
                return CascadeResult(
                    c.text, "cloud", time.perf_counter() - t0, trace
                )
            self._log(t0, "   route: clearly hard, but cloud disabled "
                          "-> best local (NVIDIA GPU)")
            return self._gpu_then_cloud(query, trace, t0)

        if r.difficulty >= CONFIG.escalate_to_gpu_difficulty:
            self._log(t0, "   route: non-trivial -> NVIDIA GPU tier")
            return self._gpu_then_cloud(query, trace, t0)

        self._log(t0, "   route: trivial -> try Tier-1 draft")
        self._start(t0, "NPU", self.npu.device)
        d = self.npu.draft(query)
        v = verify(d.text)
        self._done(t0, "NPU", f"gate: {v.reason}", d.latency_s)
        trace.append(Hop("npu", d.device, d.latency_s, f"gate: {v.reason}"))
        if v.passed:
            return CascadeResult(d.text, "npu", time.perf_counter() - t0, trace)

        self._log(t0, "   Tier-1 failed gate -> escalating")
        return self._gpu_then_cloud(query, trace, t0, fallback_text=d.text)
