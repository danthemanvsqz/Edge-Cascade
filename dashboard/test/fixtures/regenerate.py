"""Regenerate the cross-check fixture used by `dashboard/test/logfmt.test.ts`.

Reads the chosen source `.rec` file, copies its bytes to `sample.rec`, parses
it with `cascade.logfmt.parse_stream` (the canonical Python implementation),
and writes the parsed records to `sample.parsed.json`. The TS test asserts
that `parseStream(sample.rec)` matches this JSON record-for-record.

Run from the edge-cascade repo root (i.e. the parent of `dashboard/`):

    uv run python dashboard/test/fixtures/regenerate.py

The fixture is intentionally tiny and stable -- it is NOT meant to track live
`runs/` output. Re-run only when the `.rec` grammar itself changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from cascade.logfmt import parse_stream  # noqa: E402  (sys.path setup first)

FIXTURE_DIR = Path(__file__).resolve().parent
SOURCE = REPO_ROOT / "runs" / "edge-npu.rec"


def main() -> None:
    if not SOURCE.exists():
        sys.exit(f"source .rec not found: {SOURCE}")
    raw = SOURCE.read_bytes()
    records = parse_stream(raw)
    (FIXTURE_DIR / "sample.rec").write_bytes(raw)
    (FIXTURE_DIR / "sample.parsed.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(raw)} bytes, {len(records)} records")


if __name__ == "__main__":
    main()
