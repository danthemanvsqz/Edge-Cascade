"""Look-ahead pipeline runner.

Feeds a stream of tasks through the NPU-drafts / GPU-verifies speculative
controller. The NPU earns a trust window when it agrees with the GPU, then
runs solo (skipping GPU calls) until a checkpoint or a disagreement.

  python lookahead.py                 # built-in task stream
  python lookahead.py "task a" "task b" ...

Log is teed to runs/lookahead.log.
"""
from __future__ import annotations

import argparse

from cascade.lookahead import LookAhead

_DEFAULT = [
    "write a python function add(a, b) that returns their sum",
    "write a python function reverse_string(s) returning the reversed string",
    "write a python function factorial(n) iteratively",
    "write a python function is_even(n) returning a bool",
    "write a python function fib(n) returning the nth Fibonacci number",
    "write a python function gcd(a, b) using Euclid's algorithm",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="NPU/GPU look-ahead pipeline")
    ap.add_argument("--cloud", action="store_true",
                    help="allow PAID cloud escalation on verifier FAIL "
                         "(credit-guarded; off by default)")
    ap.add_argument("task", nargs="*", help="tasks; omit for the built-in set")
    args = ap.parse_args()
    tasks = args.task or _DEFAULT
    la = LookAhead(enable_cloud=args.cloud)
    res = la.run(tasks)
    print("\n--- look-ahead summary ---")
    print(f"{'#':<2} {'mode':<9} {'by':<3} {'agree':>5} {'verif':<5} "
          f"{'trust':>5}  task")
    for i, s in enumerate(res.steps, 1):
        print(f"{i:<2} {s.mode:<9} {s.answerer:<3} {s.agreement:>5.2f} "
              f"{'PASS' if s.ok else 'FAIL':<5} {s.trust_left:>5}  "
              f"{s.task[:46]}")
    print(res.speedup_note)


if __name__ == "__main__":
    main()
