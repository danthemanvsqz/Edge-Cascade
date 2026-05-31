"""Benchmark Ollama models on NL -> shell command generation.

Tests whether models that excel at git also handle general bash/CLI commands.
Same harness as git_model_bench.py: direct _generate, structural gate, Bayesian
posteriors, live dashboard events.

Run (Ollama up, ~44 min for 4-model x 20-task x 30-trial sweep):
    uv run python scripts/cli_model_bench.py
    uv run python scripts/cli_model_bench.py --models qwen2.5-coder:14b --trials 5

Decision rule: Tier-B lower-CI > 70% -> usable for CLI command generation.
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
from cascade.topology_graph import CLI_MODEL_SELECTION_GRAPH  # noqa: E402
from mcp_servers._rec import make_experiment_recorder  # noqa: E402

_URL = CONFIG.ollama_base_url.rstrip("/")
_LIVE_NODES_CHANNEL = "cascade.live.nodes"

_MODEL_NODE: dict[str, str] = {
    "qwen2.5-coder:14b":   "qwen_14b",
    "deepseek-r1:14b":     "r1_14b",
    "deepseek-coder:6.7b": "coder_6b",
    "qwen2.5-coder:7b":    "qwen_7b",
}

DEFAULT_MODELS = [
    "qwen2.5-coder:14b",
    "deepseek-r1:14b",
    "deepseek-coder:6.7b",
    "qwen2.5-coder:7b",
]

_MAX_TOKENS: dict[str, int] = {"deepseek-r1:14b": 512}
_DEFAULT_MAX_TOKENS = 96

_SYSTEM = (
    "You are a bash/shell expert. Output ONLY the single shell command, no "
    "explanation, no markdown, no code fences, no prose. One line."
)

TASKS: dict[str, dict] = {
    # --- Tier A: simple ---
    "list_py_recursive": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nList all .py files recursively in the current directory.",
        "must_start": "",
        "must_contain": [".py"],
        "alt_pass": [["find", "*.py"], ["Get-ChildItem", ".py"]],
    },
    "count_lines_file": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nCount the number of lines in file.txt.",
        "must_start": "",
        "must_contain": ["file.txt"],
        "alt_pass": [["wc", "-l"], ["Get-Content"], ["cat"]],
    },
    "show_processes": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nShow all currently running processes.",
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["ps"], ["Get-Process"], ["tasklist"]],
    },
    "show_disk_usage": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nShow disk usage for all mounted filesystems "
                  "in human-readable format.",
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["df", "-h"], ["Get-PSDrive"], ["du"]],
    },
    "print_working_dir": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nPrint the current working directory.",
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["pwd"], ["Get-Location"], ["cd"]],
    },
    # --- Tier B: medium ---
    "grep_error_recursive": {
        "tier": "B",
        "prompt": (
            f"{_SYSTEM}\n\nFind all files containing the string 'ERROR' "
            "recursively in the current directory."
        ),
        "must_start": "",
        "must_contain": ["ERROR"],
        "alt_pass": [["grep", "-r"], ["Select-String", "-Recurse"], ["rg"]],
    },
    "kill_pid": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nKill the process with PID 1234.",
        "must_start": "",
        "must_contain": ["1234"],
        "alt_pass": [["kill"], ["Stop-Process", "-Id"], ["taskkill"]],
    },
    "download_url": {
        "tier": "B",
        "prompt": (
            f"{_SYSTEM}\n\nDownload the file at https://example.com/file.zip "
            "and save it as file.zip."
        ),
        "must_start": "",
        "must_contain": ["example.com", "file.zip"],
        "alt_pass": [["curl"], ["wget"], ["Invoke-WebRequest"]],
    },
    "show_path_env": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nPrint the value of the PATH environment variable.",
        "must_start": "",
        "must_contain": ["PATH"],
        "alt_pass": [["echo", "$PATH"], ["echo", "%PATH%"], ["$env:PATH"]],
    },
    "find_modified_24h": {
        "tier": "B",
        "prompt": (
            f"{_SYSTEM}\n\nFind all files modified in the last 24 hours "
            "in the current directory."
        ),
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["find", "-mtime"], ["find", "-mmin"], ["Get-ChildItem"]],
    },
    # --- Tier C: complex ---
    "top10_largest_files": {
        "tier": "C",
        "prompt": (
            f"{_SYSTEM}\n\nFind the 10 largest files in the current directory "
            "recursively, sorted by size descending."
        ),
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["find", "sort", "head"], ["du", "sort"], ["Get-ChildItem"]],
    },
    "count_pattern_logs": {
        "tier": "C",
        "prompt": (
            f"{_SYSTEM}\n\nCount the total number of lines matching 'ERROR' "
            "across all .log files in the current directory."
        ),
        "must_start": "",
        "must_contain": ["ERROR", ".log"],
        "alt_pass": [["grep", "-c"], ["grep", "-r"], ["Select-String"]],
    },
    "compress_directory": {
        "tier": "C",
        "prompt": (
            f"{_SYSTEM}\n\nCompress the directory src/ into a gzip tarball "
            "named src.tar.gz."
        ),
        "must_start": "",
        "must_contain": ["src", ".tar.gz"],
        "alt_pass": [["tar", "-czf"], ["Compress-Archive"]],
    },
    "top5_cpu_processes": {
        "tier": "C",
        "prompt": (
            f"{_SYSTEM}\n\nShow the top 5 processes consuming the most CPU."
        ),
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["ps", "sort", "head"], ["top", "-b"], ["Get-Process", "Sort"]],
    },
    "replace_string_txt": {
        "tier": "C",
        "prompt": (
            f"{_SYSTEM}\n\nReplace every occurrence of 'foo' with 'bar' in "
            "all .txt files in the current directory, in-place."
        ),
        "must_start": "",
        "must_contain": ["foo", "bar", ".txt"],
        "alt_pass": [["sed", "-i"], ["perl"], ["Get-ChildItem"]],
    },
    # --- extra tasks to reach 20 ---
    "create_dir": {
        "tier": "A",
        "prompt": f"{_SYSTEM}\n\nCreate a directory named myproject.",
        "must_start": "",
        "must_contain": ["myproject"],
        "alt_pass": [["mkdir"], ["New-Item"]],
    },
    "show_file_permissions": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nShow the permissions of all files in the current directory.",
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["ls", "-l"], ["Get-Acl"], ["stat"]],
    },
    "find_empty_dirs": {
        "tier": "B",
        "prompt": f"{_SYSTEM}\n\nFind all empty directories recursively.",
        "must_start": "",
        "must_contain": [],
        "alt_pass": [["find", "-empty", "-type", "d"], ["Get-ChildItem"]],
    },
    "tail_follow_log": {
        "tier": "A",
        "prompt": (
            f"{_SYSTEM}\n\nContinuously follow the last 20 lines of app.log "
            "as it grows."
        ),
        "must_start": "",
        "must_contain": ["app.log"],
        "alt_pass": [["tail", "-f"], ["tail", "-n", "20"], ["Get-Content", "-Wait"]],
    },
    "sort_unique_count": {
        "tier": "C",
        "prompt": (
            f"{_SYSTEM}\n\nRead words.txt, sort the lines, remove duplicates, "
            "and count how many unique lines there are."
        ),
        "must_start": "",
        "must_contain": ["words.txt"],
        "alt_pass": [["sort", "uniq", "wc"], ["sort", "-u"], ["Get-Content", "Sort-Object"]],
    },
}


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```\w*$", re.MULTILINE)


def _extract_command(text: str) -> str:
    cleaned = _THINK_RE.sub("", text).strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    for line in cleaned.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def gate(
    text: str,
    must_start: str,
    must_contain: list[str],
    alt_pass: list[list[str]] | None = None,
) -> bool:
    cmd = _extract_command(text)
    if not cmd:
        return False
    if must_start and not cmd.startswith(must_start):
        return False
    if all(tok in cmd for tok in must_contain):
        return True
    return any(all(tok in cmd for tok in alt) for alt in (alt_pass or []))


def _model_slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", model)


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
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
    a, b = 1 + successes, 1 + failures
    mean = a / (a + b)
    samps = sorted(random.betavariate(a, b) for _ in range(n))
    return mean, samps[int(0.025 * n)], samps[int(0.975 * n)]


def _p_beats(s_a: int, f_a: int, s_b: int, f_b: int, n: int = 100_000) -> float:
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
        cp = bench_dir / f"cli-{slug}-{task_id}-{ts}.json"
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
    ap = argparse.ArgumentParser(description="Benchmark models on NL->shell commands")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--no-pull", action="store_true")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    bench_dir = ROOT / "runs" / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    emit = make_experiment_recorder("cli-model-selection")

    n_tasks, n_trials = len(TASKS), args.trials
    est_min = len(models) * n_tasks * n_trials * 1.5 / 60
    print(f"cli-model-bench: {len(models)} models × {n_tasks} tasks × {n_trials} trials")
    print(f"estimated wall time: ~{est_min:.0f} min  (leave lid open)\n")

    r: _redis_lib.Redis | None = None
    with contextlib.suppress(Exception):
        r = _redis_lib.Redis.from_url(REDIS_URL)
        payload = json.dumps(CLI_MODEL_SELECTION_GRAPH.to_dict())
        r.set(_TOPOLOGY_STATE_KEY, payload)
        r.publish(_TOPOLOGY_CHANNEL, payload)
        print("dashboard topology -> cli_model_selection\n")

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

    baseline = next(iter(all_results))
    print("\n" + _summary_table(all_results, baseline))
    print("\nDecision rule: Tier-B lower-CI > 70% -> usable for CLI command generation.\n")

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = bench_dir / f"cli_model_bench_{ts}.json"
    summary.write_text(json.dumps(
        {"ts": time.time(), "models": models, "trials": n_trials,
         "tasks": list(TASKS.keys()), "results": all_results}, indent=2),
        encoding="utf-8")
    print(f"summary -> {summary}")


if __name__ == "__main__":
    main()
