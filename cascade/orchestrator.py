"""3-tier escalation cascade: NPU/iGPU -> NVIDIA GPU -> Claude cloud.

Routing: the Tier-1 model scores difficulty up front, so trivial asks never
spin up the GPU and known-hard asks skip straight to cloud. Every local answer
is gated by the code verifier; a failed gate escalates and the failed draft is
handed to the next tier as context.

A live log prints which physical processor is working at each phase so you can
watch the cascade hop across hardware in real time.

There is no orchestrator *object*: a run is a function pipeline. The one piece
of genuine state -- the tee logger, which owns an open file handle -- has a
lifetime, so it is managed by the `cascade_session` context manager (it also
tears the handlers down on exit; the old class never closed them). The seq for
the deterministic .rec stream is an `itertools.count()` generator, not a
hand-incremented counter.
"""
from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

from .cloud_worker import make_cloud_worker, reason_note
from .config import CONFIG
from .gpu_worker import make_gpu_worker
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


def build_logger(path: Path, verbose: bool) -> logging.Logger:
    """One logger, two handlers = tee: every record fans out to the file
    (with wall-clock timestamp, for `tail -f`) and, if verbose, stdout.

    Pure construction -- no instance state -- so it is a module function.
    """
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


@dataclass(frozen=True)
class Session:
    """Immutable handle yielded by `cascade_session`: the `run` pipeline plus
    the startup facts the CLI prints. Pure data -- no behavior."""

    run: Callable[[str], CascadeResult]
    log_path: Path
    tier1_device: str
    cloud_status: str


@contextmanager
def cascade_session(
    verbose: bool = True, enable_cloud: bool = False
) -> Iterator[Session]:
    """Open a cascade session: build the tee logger + the three tier workers,
    yield a `run(query)` pipeline, and close the logger handlers on exit.

    The workers and the .rec seq are closed over by the pipeline functions --
    state lives in the closure, not an object. The paid Tier-3 is enabled only
    via `enable_cloud` or CASCADE_ENABLE_CLOUD=1.
    """
    log_path = Path(CONFIG.log_path)
    # Deterministically-parseable sibling stream (see cascade/logfmt.py).
    # The .log tee is for humans; this .rec is what validate_log consumes.
    rec_path = log_path.with_suffix(".rec")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = build_logger(log_path, verbose)
    logger.info("=== cascade started ===")

    npu = NPUWorker()
    gpu = make_gpu_worker()
    cloud = make_cloud_worker(enabled=enable_cloud or CONFIG.enable_cloud)
    logger.info(
        f"config: Tier-1={processor(npu.device)} | "
        f"Tier-2=NVIDIA RTX 5070 Ti | Tier-3={cloud.status}"
    )
    seq = count()

    def log(t0: float, msg: str) -> None:
        logger.info(f"  [{time.perf_counter() - t0:6.2f}s] {msg}")

    def start(t0: float, tier: str, device: str) -> None:
        log(t0, f">> {tier:<7} working on  {processor(device)}")

    def done(t0: float, tier: str, note: str, dt: float) -> None:
        log(t0, f"<< {tier:<7} {note}  ({dt:.2f}s)")

    def gpu_then_cloud(
        query: str, trace: list[Hop], t0: float,
        fallback_text: str | None = None,
    ) -> CascadeResult:
        prior = fallback_text
        if gpu.available():
            start(t0, "GPU", "NVIDIA/")
            g = gpu.generate(query)
            v = verify(g.text)
            note = f"{g.tokens_per_s:.0f} tok/s; gate: {v.reason}"
            done(t0, "GPU", note, g.latency_s)
            trace.append(Hop("gpu", f"NVIDIA/{g.model}", g.latency_s, note))
            if g.available and v.passed:
                return CascadeResult(
                    g.text, "gpu", time.perf_counter() - t0, trace
                )
            if g.available:
                prior = g.text
        else:
            log(t0, "-- GPU     unavailable (Ollama not reachable) - skipped")
            trace.append(Hop("gpu", "NVIDIA/Ollama", 0.0, "unavailable - skipped"))

        if not cloud.enabled:
            log(t0, "-- CLOUD   paid tier disabled - returning best local answer")
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

        start(t0, "CLOUD", cloud.model)
        c = cloud.generate(query, prior_attempt=prior)
        done(t0, "CLOUD", reason_note(c), c.latency_s)
        trace.append(Hop("cloud", c.model, c.latency_s, reason_note(c)))
        return CascadeResult(c.text, "cloud", time.perf_counter() - t0, trace)

    def run_pipeline(query: str) -> CascadeResult:
        t0 = time.perf_counter()
        trace: list[Hop] = []
        # Log the full query (single-lined) -- log-driven repair needs the
        # complete task, not a preview.
        logger.info("---- QUERY: " + " ".join(query.split())[:2000])

        start(t0, "ROUTER", npu.device)
        r = npu.route(query)
        done(
            t0, "ROUTER",
            f"difficulty={r.difficulty:.2f} category={r.category}", r.latency_s,
        )
        trace.append(
            Hop("router", r.device, r.latency_s,
                f"difficulty={r.difficulty:.2f} category={r.category}")
        )

        if r.difficulty >= CONFIG.escalate_to_cloud_difficulty:
            if cloud.enabled:
                log(t0, "   route: clearly hard -> straight to CLOUD")
                start(t0, "CLOUD", cloud.model)
                c = cloud.generate(query)
                done(t0, "CLOUD", reason_note(c), c.latency_s)
                trace.append(Hop("cloud", c.model, c.latency_s, reason_note(c)))
                return CascadeResult(
                    c.text, "cloud", time.perf_counter() - t0, trace
                )
            log(t0, "   route: clearly hard, but cloud disabled "
                    "-> best local (NVIDIA GPU)")
            return gpu_then_cloud(query, trace, t0)

        if r.difficulty >= CONFIG.escalate_to_gpu_difficulty:
            log(t0, "   route: non-trivial -> NVIDIA GPU tier")
            return gpu_then_cloud(query, trace, t0)

        log(t0, "   route: trivial -> try Tier-1 draft")
        start(t0, "NPU", npu.device)
        d = npu.draft(query)
        v = verify(d.text)
        done(t0, "NPU", f"gate: {v.reason}", d.latency_s)
        trace.append(Hop("npu", d.device, d.latency_s, f"gate: {v.reason}"))
        if v.passed:
            return CascadeResult(d.text, "npu", time.perf_counter() - t0, trace)

        log(t0, "   Tier-1 failed gate -> escalating")
        return gpu_then_cloud(query, trace, t0, fallback_text=d.text)

    def write_record(query: str, result: CascadeResult) -> None:
        """Append one deterministic record (cascade/logfmt.py grammar). The
        free-text query/answer are length-framed, so a model answer that emits
        fake "%%REC"/timestamp lines can never corrupt the parse."""
        trace = "\n".join(
            f"{h.tier}|{h.device}|{h.latency_s:.2f}s|{h.note}"
            for h in result.trace
        )
        rec = dump_record(next(seq), {
            "query": query,
            "answer": result.answer,
            "final_tier": result.final_tier,
            "total_latency_s": f"{result.total_latency_s:.2f}",
            "trace": trace,
        })
        with open(rec_path, "a", encoding="utf-8") as fh:
            fh.write(rec)

    def run(query: str) -> CascadeResult:
        """Run the cascade and log the full outcome (trace, summary, answer)."""
        result = run_pipeline(query)
        write_record(query, result)
        logger.info("---- trace ----")
        for h in result.trace:
            logger.info(
                f"  [{h.tier:<6}] {h.device:<22} "
                f"{h.latency_s:6.2f}s  {h.note}"
            )
        logger.info(
            f"== ANSWERED BY {result.final_tier.upper()} "
            f"in {result.total_latency_s:.2f}s =="
        )
        logger.info("ANSWER:\n" + result.answer + "\n")
        return result

    try:
        yield Session(
            run=run, log_path=log_path,
            tier1_device=npu.device, cloud_status=cloud.status,
        )
    finally:
        # The tee FileHandler owns an open fd -- this lifetime was unmanaged
        # in the old class. Close + detach so a re-opened session is clean.
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
