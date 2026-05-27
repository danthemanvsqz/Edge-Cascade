"""3-tier escalation cascade: NPU/iGPU -> NVIDIA GPU -> Claude cloud.

The cascade logic now lives in ONE place -- `cascade.mesh.solve(query,
topology)` -- a transport-agnostic orchestrator (route -> NPU draft -> bounded
GPU repair loop, repair cap enforced in code). This session BINDS that core to
the live workers (`cascade.wiring.build_ops`), runs it, and -- since the CLI has
no Tier-3 agent -- on a local cap-out escalates to the PAID cloud if enabled,
else returns the best-effort "capped" signal. Every local answer is gated by the
verifier inside solve before it is trusted.

A live log prints the solve trace so you can watch the cascade hop across
hardware in real time.

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
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

from . import mesh, topologies
from .cloud_worker import make_cloud_worker, reason_note
from .config import CONFIG
from .degen_recorder import make_degen_recorder
from .gpu_worker import make_gpu_worker
from .logfmt import dump_record
from .npu_worker import make_npu_worker
from .wiring import build_ops


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

    run: Callable[..., CascadeResult]
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

    npu = make_npu_worker()
    gpu = make_gpu_worker()
    cloud = make_cloud_worker(enabled=enable_cloud or CONFIG.enable_cloud)
    logger.info(
        f"config: Tier-1={processor(npu.device)} | "
        f"Tier-2=NVIDIA RTX 5070 Ti | Tier-3={cloud.status}"
    )
    seq = count()
    # Process-stable id so replay/dashboard can tie cascade.rec records to one
    # session; seq resets to 0 per process, so it alone is not enough.
    run_id = uuid.uuid4().hex[:12]

    def log(t0: float, msg: str) -> None:
        logger.info(f"  [{time.perf_counter() - t0:6.2f}s] {msg}")

    def start(t0: float, tier: str, device: str) -> None:
        log(t0, f">> {tier:<7} working on  {processor(device)}")

    def done(t0: float, tier: str, note: str, dt: float) -> None:
        log(t0, f"<< {tier:<7} {note}  ({dt:.2f}s)")

    # PD-1 v1 → SD-2b: dedicated .rec lane for degeneration observations. One
    # recorder per session (run_id + seq live in its closure); mesh.solve calls
    # it via the observe_emit op for each draft/repair output.
    degen_path = log_path.parent / "cascade-degeneration.rec"
    degen_emit = make_degen_recorder(degen_path)
    ops = build_ops(npu, gpu, observe_emit=degen_emit)

    def run_pipeline(
        query: str, topology: str = topologies.DEFAULT_TOPOLOGY
    ) -> CascadeResult:
        """Run the cascade via the single deterministic orchestrator
        (cascade.mesh.solve) -- route -> NPU draft -> bounded GPU repair loop.
        The 2-round cap is enforced inside solve, not here. On a local cap-out
        the CLI has no Tier-3 agent, so it escalates to the PAID cloud if
        enabled, else returns the best-effort 'capped' signal."""
        t0 = time.perf_counter()
        # Log the full query (single-lined) -- log-driven repair needs the
        # complete task, not a preview.
        logger.info("---- QUERY: " + " ".join(query.split())[:2000])

        outcome = mesh.solve(query, topology, ops)
        for line in outcome.trace:
            log(t0, line)
        trace = [Hop("mesh", "-", 0.0, line) for line in outcome.trace]

        if outcome.resolved:
            return CascadeResult(
                outcome.answer, outcome.final_tier,
                time.perf_counter() - t0, trace,
            )

        # Locals exhausted (cap reached / GPU unavailable).
        if cloud.enabled:
            start(t0, "CLOUD", cloud.model)
            c = cloud.generate(query)
            done(t0, "CLOUD", reason_note(c), c.latency_s)
            trace.append(Hop("cloud", c.model, c.latency_s, reason_note(c)))
            return CascadeResult(c.text, "cloud", time.perf_counter() - t0, trace)

        log(t0, "-- locals capped and paid cloud disabled -- run with --cloud")
        msg = ("[locals exhausted (repair cap reached) and the paid cloud tier "
               "is disabled. Re-run with --cloud to escalate.]")
        trace.append(Hop("local", "none", 0.0, "capped; cloud disabled"))
        return CascadeResult(
            msg, "capped (cloud disabled)", time.perf_counter() - t0, trace)

    def write_record(
        query: str, result: CascadeResult, topology: str
    ) -> None:
        """Append one deterministic record (cascade/logfmt.py grammar). The
        free-text query/answer are length-framed, so a model answer that emits
        fake "%%REC"/timestamp lines can never corrupt the parse."""
        trace = "\n".join(
            f"{h.tier}|{h.device}|{h.latency_s:.2f}s|{h.note}"
            for h in result.trace
        )
        rec = dump_record(next(seq), {
            "ts": f"{time.time():.3f}",
            "run_id": run_id,
            "query": query,
            "answer": result.answer,
            "final_tier": result.final_tier,
            "topology": topology,
            "total_latency_s": f"{result.total_latency_s:.2f}",
            "trace": trace,
        })
        # dump_record is bytes-native (the value is UTF-8 encoded once, inside
        # logfmt) -- append in binary so there is no text-layer re-encode.
        with open(rec_path, "ab") as fh:
            fh.write(rec)

    def run(
        query: str, topology: str = topologies.DEFAULT_TOPOLOGY
    ) -> CascadeResult:
        """Run the cascade and log the full outcome (trace, summary, answer)."""
        result = run_pipeline(query, topology)
        write_record(query, result, topology)
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
