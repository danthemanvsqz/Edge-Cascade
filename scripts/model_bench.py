"""A/B different GPU (Ollama) models on the dijkstra-class task.

The 2026-05-21 review found model *capability* (not compute/prompt/substrate) is
the lever on the dijkstra gate. This harness makes the "is a smaller/different
model better?" question a one-command experiment: for each model it drives the
GPU through generate -> functional-verify -> bounded repair (cap 2) on the
RUNBOOK dijkstra task, N times, and tabulates the metric that matters.

It talks to the LOCAL Ollama (no API key) and isolates the GPU tier (no NPU
draft confound) -- so the numbers reflect the GPU model alone. `verify_functional`
runs the untrusted code in a killed subprocess, never here.

Run (Ollama up):
    uv run python scripts/model_bench.py --runs 6 \
        --models qwen2.5-coder:14b,qwen2.5-coder:7b,deepseek-coder:6.7b

Reads nothing from .env; pulls missing models via the `ollama` CLI unless
--no-pull. Writes a JSON summary under runs/bench/.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402
from cascade.feedback import CheckFailure, build_repair_prompt  # noqa: E402
from cascade.gpu_worker import _available, _generate  # noqa: E402

# Same task the probe/checks.dsl bind `dijkstra` -> drone_ok (sink-node A->E=11).
TASK = (
    "Write a Python function def dijkstra(graph, start) that returns a dict of "
    "shortest-path costs from start for a directed weighted graph given as "
    "{node: {neighbor: weight}}."
)
DEFAULT_MODELS = [
    "qwen2.5-coder:14b",          # current baseline
    "qwen2.5-coder:7b",           # smaller-Qwen test
    "deepseek-coder:6.7b",        # different coder family
]
CAP = 2  # repair-round cap, matching the mesh policy
_URL = CONFIG.ollama_base_url.rstrip("/")
_FUNC_TIMEOUT_S = 20
_CODE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    m = _CODE.search(text)
    return (m.group(1) if m else text).strip()


def verify_functional(text: str) -> dict:
    """Functional gate in a killed subprocess (mirrors mcp_servers/verify.py)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_servers._funcverify_child"],
            input=json.dumps({"text": text, "dsl": None}),
            capture_output=True, text=True, cwd=str(ROOT),
            timeout=_FUNC_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"applicable": True, "passed": False, "failures": [
            {"expr": "completes", "observed": "timeout", "requirement": ""}]}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"applicable": False, "passed": False, "failures": []}
    return json.loads(proc.stdout)


def _fails(v: dict) -> list[CheckFailure]:
    return [CheckFailure(expr=f.get("expr", ""), observed=f.get("observed", ""),
                         requirement=f.get("requirement", ""))
            for f in (v.get("failures") or [])]


def run_one(model: str, max_tokens: int) -> dict:
    """One GPU attempt with a bounded repair loop. Returns the outcome of this
    single task run for `model`."""
    toks: list[float] = []
    lats: list[float] = []
    g = _generate(_URL, model, TASK, max_tokens)
    if not g.available:
        return {"available": False}
    toks.append(g.tokens_per_s)
    lats.append(g.latency_s)
    v = verify_functional(g.text)
    if v.get("applicable") and v.get("passed"):
        return {"available": True, "resolved": True, "fresh": True,
                "rounds": 0, "tok_s": toks, "lat": lats}
    prior, fails = extract_code(g.text), _fails(v)
    for rnd in range(1, CAP + 1):
        g = _generate(_URL, model, build_repair_prompt(TASK, prior, fails), max_tokens)
        toks.append(g.tokens_per_s)
        lats.append(g.latency_s)
        v = verify_functional(g.text)
        if v.get("applicable") and v.get("passed"):
            return {"available": True, "resolved": True, "fresh": False,
                    "rounds": rnd, "tok_s": toks, "lat": lats}
        prior, fails = extract_code(g.text), _fails(v)
    return {"available": True, "resolved": False, "fresh": False,
            "rounds": CAP, "tok_s": toks, "lat": lats}


def bench_model(model: str, runs: int, max_tokens: int, pull: bool) -> dict:
    if pull:
        print(f"  pulling {model} ...", flush=True)
        subprocess.run(["ollama", "pull", model], check=False)
    if not _available(_URL, model):
        print(f"  [skip] {model}: not available (pull it / start Ollama)")
        return {"model": model, "available": False}
    rows = [run_one(model, max_tokens) for _ in range(runs)]
    ok = [r for r in rows if r.get("available")]
    fresh = sum(r.get("fresh") for r in ok)
    resolved = sum(r.get("resolved") for r in ok)
    rounds = [r["rounds"] for r in ok if r.get("resolved")]
    tok = [t for r in ok for t in r["tok_s"]]
    lat = [x for r in ok for x in r["lat"]]
    return {
        "model": model, "available": True, "runs": len(ok),
        "fresh_pass": fresh, "resolved": resolved, "capped": len(ok) - resolved,
        "avg_rounds_to_pass": round(statistics.mean(rounds), 2) if rounds else None,
        "median_tok_s": round(statistics.median(tok), 1) if tok else 0.0,
        "median_latency_s": round(statistics.median(lat), 1) if lat else 0.0,
    }


def _table(results: list[dict]) -> str:
    h = (f"{'model':<28}{'runs':>5}{'fresh':>7}{'resolved':>10}"
         f"{'capped':>7}{'rounds':>8}{'tok/s':>8}{'lat_s':>7}")
    lines = [h, "-" * len(h)]
    for r in results:
        if not r.get("available"):
            lines.append(f"{r['model']:<28}{'(unavailable)':>45}")
            continue
        n = r["runs"] or 1
        lines.append(
            f"{r['model']:<28}{r['runs']:>5}{r['fresh_pass']:>4}/{n:<2}"
            f"{r['resolved']:>7}/{n:<2}{r['capped']:>7}"
            f"{str(r['avg_rounds_to_pass']):>8}{r['median_tok_s']:>8}"
            f"{r['median_latency_s']:>7}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B GPU models on the dijkstra gate")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated Ollama tags")
    ap.add_argument("--runs", type=int, default=6, help="runs per model (stochastic)")
    ap.add_argument("--max-tokens", type=int, default=CONFIG.gpu_max_new_tokens,
                    help="num_predict per generate (raise for deepseek-r1 <think>)")
    ap.add_argument("--no-pull", action="store_true", help="don't `ollama pull`")
    ap.add_argument("--json", default="", help="write summary JSON to this path")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"benchmarking {len(models)} model(s) x {args.runs} runs on the "
          f"dijkstra gate (cap={CAP})\n")
    results = []
    for m in models:
        print(f"== {m} ==", flush=True)
        results.append(bench_model(m, args.runs, args.max_tokens, not args.no_pull))

    print("\n" + _table(results))
    print("\nfresh = GPU got it right with NO repair (the cleanest quality "
          "signal); resolved = passed within the 2-round cap; spend $0 (local).")

    out = args.json or str(
        ROOT / "runs" / "bench" /
        f"model_bench_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(
        {"task": TASK, "cap": CAP, "runs_per_model": args.runs,
         "ts": time.time(), "results": results}, indent=2), encoding="utf-8")
    print(f"\nsummary -> {out}")


if __name__ == "__main__":
    main()
