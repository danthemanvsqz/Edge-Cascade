"""Dedicated `.rec` lane for PD-1 degeneration observations.

`make_degen_recorder(path)` returns an `emit(tier, result)` closure that
appends ONE logfmt record per call to `path`. Single-writer / append-only:
the `seq` counter and `run_id` live in the closure, so there is no object
and no manual read-modify-write of the counter. The SD-2b dashboard panel
tails `runs/cascade-degeneration.rec` -- a separate file from
`cascade.rec` so the panel doesn't have to grep cascade.log for
`degen[<tier>]:` lines.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from itertools import count
from pathlib import Path

from cascade.degeneration import DegenerationResult
from cascade.logfmt import dump_record


def make_degen_recorder(
    path: Path,
) -> Callable[[str, DegenerationResult], None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = count()
    run_id = uuid.uuid4().hex[:12]

    def emit(tier: str, result: DegenerationResult) -> None:
        fields = {
            "server": "cascade-degeneration",
            "tool": "observe",
            "ts": f"{time.time():.3f}",
            "run_id": run_id,
            "tier": tier,
            "score": f"{result.score:.2f}",
            "degraded": "true" if result.degraded else "false",
            "reasons": json.dumps(result.reasons, ensure_ascii=False),
            "trigram_repeat": f"{result.features['trigram_repeat']:.4f}",
            "max_sent_repeat": f"{result.features['max_sent_repeat']:.2f}",
            "distinct_sent_ratio": f"{result.features['distinct_sent_ratio']:.4f}",
            "ttr": f"{result.features['ttr']:.4f}",
        }
        current_seq = next(seq)
        rec = dump_record(current_seq, fields)
        with open(path, "ab") as fh:
            fh.write(rec)

    return emit
