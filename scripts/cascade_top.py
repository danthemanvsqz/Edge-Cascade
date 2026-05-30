"""Live `top`-style view of active edge-cascade tasks (debug consumer).

Polls the shared Flower probe (`cascade.flower_activity.snapshot`) at ~2 Hz and
redraws which chain node is spinning right now -- the in-progress signal the
`runs/cascade.rec` stream can't give (it only records completed outcomes). Run
it in a spare terminal while a solve grinds:

    uv run python scripts/cascade_top.py
    uv run python scripts/cascade_top.py --url http://otherbox:5555

Needs Flower up (scripts/edge-cli.ps1 -Canvas, or `celery -A cascade.celery_app
flower`). Read-only: snapshot() never raises, so a down Flower just shows idle.
"""
from __future__ import annotations

import argparse
import time

from cascade.flower_activity import FLOWER_URL, snapshot


def main() -> None:
    ap = argparse.ArgumentParser(description="Live view of active edge-cascade tasks.")
    ap.add_argument("--url", default=FLOWER_URL, help=f"Flower base URL (default: {FLOWER_URL}).")
    args = ap.parse_args()

    try:
        while True:
            tasks = snapshot(base_url=args.url)
            print("\033[2J\033[H", end="")  # clear screen + home cursor
            print(f"edge-cascade top -- {time.strftime('%H:%M:%S')}")
            if not tasks:
                print("idle -- no cascade tasks running")
            else:
                print(f"{'NODE':<12} {'TIER':<7} {'RUNTIME':>8}  WORKER")
                for t in sorted(tasks, key=lambda t: t.runtime_s, reverse=True):
                    print(f"{t.node:<12} {t.tier:<7} {t.runtime_s:7.1f}s  {t.worker}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
