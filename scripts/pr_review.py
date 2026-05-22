"""Credit-guarded PR code review via the paid Anthropic API.

The *sanctioned* spend lane — the cascade build path stays $0. A review goes
through the SAME credit guard (cascade.credit_guard) + cost math the cascade
uses, and is recorded to runs/edge-review.rec — a SEPARATE stream — so the
cascade's $0 SPEND panel is never conflated with review spend. If the guard is
tripped or no key is present, it skips cleanly (exit 0) and NEVER blocks a push.

Run:
    uv run python scripts/pr_review.py <PR#>            # preview to stdout
    uv run python scripts/pr_review.py <PR#> --post     # also post a PR comment
    # CASCADE_GH overrides the gh binary if it isn't on PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade import reviewer  # noqa: E402
from cascade.config import CONFIG  # noqa: E402
from cascade.credit_guard import CreditGuard  # noqa: E402
from mcp_servers._rec import make_recorder  # noqa: E402

_GH = os.environ.get("CASCADE_GH", "gh")
_REC = make_recorder("edge-review")


def _gh(*args: str) -> str:
    return subprocess.run([_GH, *args], capture_output=True, text=True,
                          cwd=str(ROOT)).stdout


def _verdict(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip().upper().startswith("VERDICT:"):
            return line.strip()
    return "VERDICT: (unstated)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Credit-guarded PR review")
    ap.add_argument("pr", help="PR number")
    ap.add_argument("--model", default=CONFIG.review_model)
    ap.add_argument("--post", action="store_true",
                    help="post the review as a PR comment (default: stdout only)")
    args = ap.parse_args()

    # The guard: one review = one paid call, capped by review_usd_budget. No key
    # -> disabled -> skip (never block).
    guard = CreditGuard(max_calls=1, usd_budget=CONFIG.review_usd_budget,
                        enabled=bool(CONFIG.anthropic_api_key))
    if not guard.allowed:
        print(f"[pr_review] skipped: guard not allowed ({guard.state()})")
        return 0

    diff = _gh("pr", "diff", args.pr)
    if not diff.strip():
        print(f"[pr_review] no diff for PR #{args.pr}; skipping")
        return 0
    meta = _gh("pr", "view", args.pr, "--json", "title,body")
    title, body = "", ""
    try:
        m = json.loads(meta)
        title, body = m.get("title", ""), m.get("body", "")
    except (ValueError, TypeError):
        pass

    prompt = reviewer.build_prompt(diff, title, body, CONFIG.review_max_diff_bytes)

    import anthropic
    res = reviewer.review(anthropic.Anthropic(), args.model,
                          CONFIG.cloud_max_tokens, prompt)
    cost = reviewer.est_cost_usd(res)
    guard.charge(cost)

    # Record to the SEPARATE review stream (cascade spend stays $0).
    _REC("review", {
        "args": json.dumps({"pr": args.pr, "model": args.model}),
        "ok": "true" if res.available else "false",
        "result": json.dumps({
            "model": res.model, "est_cost_usd": round(cost, 6),
            "in_tok": res.input_tokens, "out_tok": res.output_tokens,
            "verdict": _verdict(res.text)}),
        "latency_ms": f"{res.latency_s * 1000:.1f}",
    })

    header = (f"### 🤖 Claude API review — `{res.model}` "
              f"(est ${cost:.4f}, {res.input_tokens}+{res.output_tokens} tok)\n\n")
    out = header + res.text
    print(out)
    print(f"\n[pr_review] est_cost=${cost:.4f}  guard={guard.state()}")

    if args.post and res.available:
        _gh("pr", "comment", args.pr, "--body", out)
        print(f"[pr_review] posted to PR #{args.pr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
