"""git ship — the PR-handbook trigger as one local command (key stays in .env).

Push the current feature branch, open a PR if none exists, then fire the
credit-guarded review. Makes "open the PR on first push + fire the review" a
deterministic command instead of relying on the agent remembering — without the
GitHub-Action key exposure. The review's daily/round/per-call guards still apply.

    uv run python scripts/ship.py                 # --fill the PR from commits
    uv run python scripts/ship.py --title "..." --body "..."
    git config alias.ship '!uv run python scripts/ship.py'   # then: git ship
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_GH = os.environ.get("CASCADE_GH", "gh")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="push + open PR + fire review")
    ap.add_argument("--title", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--no-review", action="store_true",
                    help="open the PR but skip the paid review")
    args = ap.parse_args()

    branch = _run("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch in ("main", "master", "HEAD"):
        print(f"[ship] refusing: on '{branch}' — ship from a feature branch.")
        return 1

    print(f"[ship] pushing {branch} ...")
    push = _run("git", "push", "-u", "origin", branch)
    if push.returncode != 0:
        print(push.stderr.strip() or "[ship] push failed")
        return 1

    existing = _run(_GH, "pr", "list", "--head", branch, "--json", "number").stdout
    pr = ""
    try:
        rows = json.loads(existing or "[]")
        pr = str(rows[0]["number"]) if rows else ""
    except (ValueError, KeyError, IndexError, TypeError):
        pr = ""

    if not pr:
        create = ["pr", "create", "--head", branch]
        create += (["--title", args.title, "--body", args.body]
                   if args.title else ["--fill"])
        print("[ship] opening PR ...")
        if _run(_GH, *create).returncode != 0:
            print("[ship] gh pr create failed")
            return 1
        rows = json.loads(
            _run(_GH, "pr", "list", "--head", branch, "--json", "number").stdout
            or "[]")
        pr = str(rows[0]["number"]) if rows else ""

    print(f"[ship] PR #{pr}")
    if args.no_review or not pr:
        return 0

    # Fire the review (its own daily/round/per-call guards decide whether to spend).
    print(f"[ship] firing review on PR #{pr} ...")
    rv = subprocess.run([sys.executable, str(ROOT / "scripts" / "pr_review.py"),
                         pr, "--post"], cwd=str(ROOT))
    return rv.returncode


if __name__ == "__main__":
    sys.exit(main())
