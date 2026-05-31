"""Benchmark Ollama models on NL -> git command generation.

Tests whether deepseek-r1:14b (reasoning) outperforms code-specialised models
on git command recall. No repair loop: commands are short and deterministic;
pass/fail on a per-task structural gate is the signal.

Run (Ollama up, ~44 min for full 4-model x 20-task x 30-trial sweep):
    uv run python scripts/git_model_bench.py
    uv run python scripts/git_model_bench.py --models deepseek-r1:14b,qwen2.5-coder:14b --trials 10

Decision rule: lower-95%-CI > 70% on Tier-B -> wire that model in;
otherwise pull qwen2.5:14b (general-purpose) as a follow-up.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import random
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis as _redis_lib  # noqa: E402

from cascade.celery_app import _TOPOLOGY_CHANNEL, _TOPOLOGY_STATE_KEY, REDIS_URL  # noqa: E402
from cascade.config import CONFIG  # noqa: E402
from cascade.gpu_worker import _available, _generate  # noqa: E402
from cascade.topology_graph import GIT_MODEL_SELECTION_GRAPH  # noqa: E402
from mcp_servers._rec import make_experiment_recorder  # noqa: E402

_URL = CONFIG.ollama_base_url.rstrip("/")
_LIVE_NODES_CHANNEL = "cascade.live.nodes"

# Maps Ollama model tag -> dashboard node ID in GIT_MODEL_SELECTION_GRAPH
_MODEL_NODE: dict[str, str] = {
    "qwen2.5-coder:14b":   "qwen_14b",
    "deepseek-r1:14b":     "r1_14b",
    "deepseek-coder:6.7b": "coder_6b",
    "qwen2.5-coder:7b":    "qwen_7b",
}

DEFAULT_MODELS = [
    "qwen2.5-coder:14b",    # baseline (production pipeline model)
    "deepseek-r1:14b",      # reasoning challenger
    "deepseek-coder:6.7b",  # small-coder control
    "qwen2.5-coder:7b",     # mid-size control
]

# deepseek-r1 emits <think>...</think> before the answer; give it room
_MAX_TOKENS: dict[str, int] = {"deepseek-r1:14b": 512}
_DEFAULT_MAX_TOKENS = 64

_SYSTEM = (
    "You are a git expert. Output ONLY the single git command, no explanation, "
    "no markdown, no code fences, no prose. One line starting with 'git'."
)

# Tier A = simple, B = medium (target failure-rich band), C = complex
TASKS: dict[str, dict] = {
    # --- Tier A ---
    "log_oneline_10": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nShow the last 10 commits, one per line with hash and message.",
        "must_start": "git log",
        "must_contain": ["--oneline"],
    },
    "stage_tracked": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nStage all modified and deleted tracked files (not untracked).",
        "must_start": "git add",
        "must_contain": ["-u"],
    },
    "current_branch": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nPrint only the name of the current branch.",
        "must_start": "git",
        "must_contain": ["branch", "--show-current"],
        "alt_pass": [["rev-parse", "--abbrev-ref"]],
    },
    "stash_with_msg": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nStash all current changes with the message 'wip'.",
        "must_start": "git stash",
        "must_contain": ["wip"],
    },
    "list_local_branches": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nList all local branches.",
        "must_start": "git branch",
        "must_contain": [],
    },
    # --- Tier B ---
    "cherry_pick": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nCherry-pick commit abc1234 onto the current branch.",
        "must_start": "git cherry-pick",
        "must_contain": ["abc1234"],
    },
    "undo_last_keep_staged": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nUndo the last commit but keep its changes staged.",
        "must_start": "git reset",
        "must_contain": ["--soft", "HEAD~1"],
    },
    "diff_two_branches": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nShow the diff between branch 'feature' and branch 'main'.",
        "must_start": "git diff",
        "must_contain": ["feature", "main"],
    },
    "log_author_week": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nList commits authored by 'Alice' in the last 7 days, "
                  "one per line.",
        "must_start": "git log",
        "must_contain": ["--author", "Alice", "--since"],
    },
    "rebase_interactive_3": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nStart an interactive rebase covering the last 3 commits.",
        "must_start": "git rebase",
        "must_contain": ["-i", "HEAD~3"],
    },
    # --- Tier C ---
    "bisect_start_bad": {
        "tier": "C",
        "prompt": f"{_SYSTEM}\n\nBegin a bisect session and mark the current commit as bad.",
        "must_start": "git bisect",
        "must_contain": ["bad"],
    },
    "signed_annotated_tag": {
        "tier": "C",
        "prompt": f"{_SYSTEM}\n\nCreate a signed annotated tag v1.0.0 with the message 'release'.",
        "must_start": "git tag",
        "must_contain": ["-s", "v1.0.0", "-m"],
    },
    "squash_merge_main": {
        "tier": "C",
        "prompt": f"{_SYSTEM}\n\nMerge branch 'feature' into the current branch, "
                  "squashing all feature commits into one.",
        "must_start": "git merge",
        "must_contain": ["--squash", "feature"],
    },
    "reflog_last_20": {
        "tier": "C",
        "prompt": f"{_SYSTEM}\n\nShow the last 20 reflog entries.",
        "must_start": "git reflog",
        "must_contain": ["20"],
    },
    "amend_message_only": {
        "tier": "C",
        "prompt": f"{_SYSTEM}\n\nAmend only the last commit's message to 'fix: typo' "
                  "without touching staged files.",
        "must_start": "git commit",
        "must_contain": ["--amend", "-m"],
    },
    # extra tasks to reach 20 (mix of tiers)
    "show_commit": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nShow the full details of commit abc1234.",
        "must_start": "git show",
        "must_contain": ["abc1234"],
    },
    "fetch_prune": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nFetch from origin and remove any remote-tracking "
                  "branches that no longer exist.",
        "must_start": "git fetch",
        "must_contain": ["--prune"],
    },
    "tag_list": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nList all tags in the repository.",
        "must_start": "git tag",
        "must_contain": [],
    },
    "set_upstream": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nPush the current branch to origin and set it as "
                  "the upstream tracking branch.",
        "must_start": "git push",
        "must_contain": ["-u", "origin"],
    },
    "clean_untracked": {
        "tier": "C",
        "prompt": f"{_SYSTEM}\n\nRemove all untracked files and directories, "
                  "including those listed in .gitignore.",
        "must_start": "git clean",
        "must_contain": ["-fdx"],
    },
}

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```\w*$", re.MULTILINE)


def _extract_command(text: str) -> str:
    """Strip <think> blocks and code fences; return first line starting with 'git'."""
    cleaned = _THINK_RE.sub("", text).strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    # Prefer any line that starts with 'git'
    for line in cleaned.splitlines():
        line = line.strip()
        if line.startswith("git"):
            return line
    # Fallback: first non-blank line (lets the gate fail gracefully)
    for line in cleaned.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def gate(
    text: str,
    must_start: str,
    must_contain: list[str],
    alt_pass: list[list[str]] | None = None,
) -> bool:
    """True if cmd starts with must_start AND satisfies must_contain OR any alt_pass list."""
    cmd = _extract_command(text)
    if not cmd.startswith(must_start):
        return False
    if all(tok in cmd for tok in must_contain):
        return True
    return any(all(tok in cmd for tok in alt) for alt in (alt_pass or []))


def _model_slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", model)


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """Hold a Windows wake-lock for the duration. Lid-close still halts CPU."""
    kernel = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if kernel is not None:
        with contextlib.suppress(Exception):
            kernel.SetThreadExecutionState(0x80000001)
    try:
        yield
    finally:
        if kernel is not None:
            with contextlib.suppress(Exception):
                kernel.SetThreadExecutionState(0x80000000)


def _node_state(r: _redis_lib.Redis, node: str, state: str) -> None:
    with contextlib.suppress(Exception):
        r.publish(_LIVE_NODES_CHANNEL, json.dumps({"node": node, "state": state}))


def _beta_stats(
    successes: int, failures: int, n: int = 100_000
) -> tuple[float, float, float]:
    """Beta(1+s, 1+f) posterior -> (mean, ci_lo_95, ci_hi_95)."""
    a, b = 1 + successes, 1 + failures
    mean = a / (a + b)
    samps = sorted(random.betavariate(a, b) for _ in range(n))
    return mean, samps[int(0.025 * n)], samps[int(0.975 * n)]


def _p_beats(s_a: int, f_a: int, s_b: int, f_b: int, n: int = 100_000) -> float:
    """P(model_A pass-rate > model_B) via MC."""
    return sum(
        random.betavariate(1 + s_a, 1 + f_a) > random.betavariate(1 + s_b, 1 + f_b)
        for _ in range(n)
    ) / n


def run_model(
    model: str, trials: int, emit, bench_dir: Path,
    r: _redis_lib.Redis | None = None,
) -> dict[str, dict]:
    max_toks = _MAX_TOKENS.get(model, _DEFAULT_MAX_TOKENS)
    slug = _model_slug(model)
    node_id = _MODEL_NODE.get(model, "qwen_14b")
    results: dict[str, dict] = {}

    for task_id, task in TASKS.items():
        passes = 0
        latencies: list[float] = []
        tok_rates: list[float] = []

        for trial in range(trials):
            if r:
                _node_state(r, node_id, "active")
            g = _generate(_URL, model, task["prompt"], max_toks)
            if not g.available:
                if r:
                    _node_state(r, node_id, "idle")
                print(f"    [{task_id}] trial {trial}: unavailable", flush=True)
                continue
            latencies.append(g.latency_s)
            tok_rates.append(g.tokens_per_s)
            if r:
                _node_state(r, "gate", "active")
            passed = gate(g.text, task["must_start"], task["must_contain"],
                          task.get("alt_pass"))
            if r:
                _node_state(r, "gate", "idle")
                _node_state(r, node_id, "idle")
            passes += int(passed)
            emit("trial", {
                "model": model,
                "task": task_id,
                "tier": task["tier"],
                "trial": str(trial),
                "passed": str(passed).lower(),
                "latency_s": f"{g.latency_s:.3f}",
                "tokens_per_s": f"{g.tokens_per_s:.1f}",
            })

        n_ran = len(latencies)
        results[task_id] = {
            "tier": task["tier"],
            "passes": passes,
            "trials": n_ran,
            "mean_lat_s": round(sum(latencies) / max(n_ran, 1), 2),
            "mean_tok_s": round(sum(tok_rates) / max(n_ran, 1), 1),
        }
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        cp = bench_dir / f"git-{slug}-{task_id}-{ts}.json"
        cp.write_text(json.dumps(
            {"model": model, "task_id": task_id, "task": task,
             "result": results[task_id]}, indent=2), encoding="utf-8")
        print(f"  {task['tier']} {task_id}: {passes}/{n_ran}  ({cp.name})", flush=True)

    return results


def _tier_agg(results: dict[str, dict], tier: str) -> tuple[int, int]:
    s = sum(v["passes"] for v in results.values() if v["tier"] == tier)
    f = sum(v["trials"] - v["passes"] for v in results.values() if v["tier"] == tier)
    return s, f


def _summary_table(all_results: dict[str, dict], baseline: str) -> str:
    tiers = ["A", "B", "C"]
    header = f"{'model':<28}" + "".join(f"  tier-{t}(mean [95%CI] P>base)" for t in tiers)
    lines = [header, "-" * len(header)]
    base_stats = {t: _tier_agg(all_results.get(baseline, {}), t) for t in tiers}

    for model, results in all_results.items():
        row = f"{model:<28}"
        for tier in tiers:
            s, f = _tier_agg(results, tier)
            mean, lo, hi = _beta_stats(s, f)
            if model == baseline:
                p_str = "(base)"
            else:
                bs, bf = base_stats[tier]
                p = _p_beats(s, f, bs, bf)
                p_str = f"P>{p:.2f}"
            row += f"  {mean*100:5.1f}%[{lo*100:.0f},{hi*100:.0f}] {p_str}"
        lines.append(row)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark models on NL->git commands")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated Ollama tags")
    ap.add_argument("--trials", type=int, default=30, help="trials per task")
    ap.add_argument("--no-pull", action="store_true", help="skip ollama pull")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    bench_dir = ROOT / "runs" / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    emit = make_experiment_recorder("git-model-selection")

    n_tasks, n_trials = len(TASKS), args.trials
    est_min = len(models) * n_tasks * n_trials * 1.1 / 60
    print(f"git-model-bench: {len(models)} models × {n_tasks} tasks × {n_trials} trials")
    print(f"estimated wall time: ~{est_min:.0f} min  (leave lid open)\n")

    # Push the experiment topology to the dashboard and wire live node events
    r: _redis_lib.Redis | None = None
    with contextlib.suppress(Exception):
        r = _redis_lib.Redis.from_url(REDIS_URL)
        payload = json.dumps(GIT_MODEL_SELECTION_GRAPH.to_dict())
        r.set(_TOPOLOGY_STATE_KEY, payload)
        r.publish(_TOPOLOGY_CHANNEL, payload)
        print("dashboard topology -> git_model_selection\n")

    all_results: dict[str, dict] = {}
    with keep_awake():
        for model in models:
            if not args.no_pull:
                subprocess.run(["ollama", "pull", model], check=False)
            if not _available(_URL, model):
                print(f"[skip] {model}: not available")
                continue
            print(f"\n== {model} ==", flush=True)
            all_results[model] = run_model(model, n_trials, emit, bench_dir, r)

    if not all_results:
        print("no models ran — check Ollama is up and models are pulled")
        return

    baseline = next(iter(all_results))  # first available model
    print("\n" + _summary_table(all_results, baseline))
    print("\nDecision rule: Tier-B lower-CI > 70% -> wire that model in;"
          " otherwise pull qwen2.5:14b (general) as follow-up.\n")

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = bench_dir / f"git_model_bench_{ts}.json"
    summary.write_text(json.dumps(
        {"ts": time.time(), "models": models, "trials": n_trials,
         "tasks": list(TASKS.keys()), "results": all_results}, indent=2),
        encoding="utf-8")
    print(f"summary -> {summary}")


if __name__ == "__main__":
    main()
