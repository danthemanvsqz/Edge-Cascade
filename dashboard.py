"""Live terminal health dashboard for the cascade -- read-only over runs/*.rec.

While a launched session (Tier 3) drives the mesh, this answers "is it
actually running, are the MCPs producing results, what failed, what escalated,
and -- the headline invariant -- did we spend anything?" entirely from the
deterministic recorder, never from chat narration.

Two layers, deliberately split so a later `rich.Live` swap touches only the
second:
  * `compute_metrics(records, gap)` -- PURE: records -> a metrics dict. The
    unit-tested core. Reuses replay.py's parse/merge/episode layer.
  * `render(metrics)` -- PURE: metrics dict -> the screen string.
The loop just re-reads, recomputes, clears (ANSI), redraws every --interval.

Spend is the load-bearing panel: edge-cloud generation calls + Σ cost MUST be
zero on the local-first policy; it renders RED the instant it is not.

Offline dev tool like validate_log.py / replay.py -- never in the hot path.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import sys
import time

from replay import RUNS, load_streams, split_episodes, tag_and_merge

CLEAR = "\x1b[2J\x1b[H"
_RED, _GRN, _DIM, _OFF = "\x1b[31m", "\x1b[32m", "\x1b[2m", "\x1b[0m"
DEFAULT_INTERVAL = 2.0
DEFAULT_GAP = 30.0
SERVERS = ("edge-npu", "edge-gpu", "edge-verify", "edge-cloud")


# ---- pure helpers -----------------------------------------------------------

def _f(rec: dict, key: str) -> float | None:
    try:
        return float(rec[key])
    except (KeyError, ValueError, TypeError):
        return None


def _result(rec: dict):
    try:
        return json.loads(rec["result"])
    except (KeyError, TypeError, ValueError):
        return None


def _pctl(values: list[float], q: float) -> float:
    """Nearest-rank percentile (stdlib only; total on empty -> 0.0)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(q / 100 * (len(s) - 1))))
    return s[k]


def _final_tier(ep: list[dict]) -> str:
    """Episode outcome: exact from a cascade.rec record, else inferred from
    the per-server tool sequence (heuristic -- documented as such)."""
    for r in ep:
        if "final_tier" in r:
            return r["final_tier"]
    tools = {(r.get("_src"), r.get("tool")) for r in ep}
    repairs = sum(r.get("tool") == "repair_prompt" for r in ep)
    last_func = next(
        (_result(r) for r in reversed(ep)
         if r.get("tool") == "verify_functional"), None)
    if isinstance(last_func, dict) and last_func.get("passed"):
        if ("edge-gpu", "generate") in tools:
            return "gpu"
        return "npu"
    if repairs >= 2:
        return "capped->tier3"
    if ("edge-cloud", "ask") in tools or ("edge-cloud", "generate") in tools:
        return "cloud"
    return "unresolved"


# ---- the pure core ----------------------------------------------------------

def compute_metrics(records: list[dict], gap: float = DEFAULT_GAP) -> dict:
    """records -> a metrics dict. Pure, total (tolerates legacy records that
    lack ts/run_id/ok). Episode-level numbers reuse replay.split_episodes."""
    now = time.time()
    per_server: dict[str, dict] = {}
    for srv in SERVERS:
        rs = [r for r in records if r.get("_src") == srv]
        lat = [v for v in (_f(r, "latency_ms") for r in rs) if v is not None]
        ts = [float(r["ts"]) for r in rs if r.get("ts")]
        ok = sum(r.get("ok") == "true" for r in rs)
        err = sum(r.get("ok") == "false" for r in rs)
        per_server[srv] = {
            "calls": len(rs),
            "ok": ok,
            "err": err,
            "success_pct": (100.0 * ok / (ok + err)) if (ok + err) else None,
            "age_s": (now - max(ts)) if ts else None,
            "p50_ms": _pctl(lat, 50),
            "p95_ms": _pctl(lat, 95),
            "max_ms": max(lat) if lat else 0.0,
        }

    drafts = [r for r in records if r.get("tool") == "draft"]
    syntax = [r for r in records if r.get("tool") == "verify_syntax"]
    trunc = sum(
        isinstance(_result(r), dict) and _result(r).get("has_code") is False
        for r in syntax)
    routes = [_result(r) for r in records if r.get("tool") == "route"]
    diff_hist: dict[str, int] = {}
    for rt in routes:
        if isinstance(rt, dict):
            diff_hist[rt.get("category", "?")] = (
                diff_hist.get(rt.get("category", "?"), 0) + 1)

    func = [r for r in records if r.get("tool") == "verify_functional"]
    func_fail = sum(
        isinstance(_result(r), dict) and _result(r).get("passed") is False
        for r in func)
    syntax_fail = sum(
        isinstance(_result(r), dict) and _result(r).get("passed") is False
        for r in syntax)
    errors: dict[str, int] = {}
    for r in records:
        if r.get("ok") == "false":
            kind = str(r.get("error", "?")).split(":")[0]
            errors[kind] = errors.get(kind, 0) + 1
    gpu_unavail = sum(
        r.get("tool") == "generate"
        and isinstance(_result(r), dict)
        and _result(r).get("available") is False
        for r in records)

    episodes = split_episodes(records, gap)
    round_hist: dict[int, int] = {}
    cap_hits = 0
    tier_hist: dict[str, int] = {}
    for ep in episodes:
        rounds = sum(r.get("tool") == "repair_prompt" for r in ep)
        round_hist[rounds] = round_hist.get(rounds, 0) + 1
        tier = _final_tier(ep)
        tier_hist[tier] = tier_hist.get(tier, 0) + 1
        if tier == "capped->tier3":
            cap_hits += 1
    gpu_gen = sum(r.get("tool") == "generate" for r in records)

    cloud = [r for r in records if r.get("_src") == "edge-cloud"]
    cloud_gen = [r for r in cloud if r.get("tool") in ("ask", "generate")]
    spend = 0.0
    for r in cloud:
        res = _result(r)
        if isinstance(res, dict):
            spend += float(res.get("est_cost_usd", 0.0) or 0.0)

    return {
        "total_records": len(records),
        "episodes": len(episodes),
        "per_server": per_server,
        "producing": {
            "drafts": len(drafts),
            "trunc": trunc,
            "trunc_pct": (100.0 * trunc / len(syntax)) if syntax else 0.0,
            "route_categories": diff_hist,
        },
        "failures": {
            "syntax_fail": syntax_fail,
            "func_fail": func_fail,
            "tool_errors": errors,
            "gpu_unavailable": gpu_unavail,
        },
        "escalations": {
            "gpu_generate_calls": gpu_gen,
            "repair_round_hist": round_hist,
            "cap_hits": cap_hits,
            "final_tier": tier_hist,
        },
        "spend": {
            "cloud_calls": len(cloud_gen),
            "usd": round(spend, 4),
            "clean": not cloud_gen and spend == 0.0,
        },
    }


# ---- pure render ------------------------------------------------------------

def _age(s: float | None) -> str:
    if s is None:
        return "  --  "
    if s < 90:
        return f"{s:4.0f}s "
    return f"{s / 60:4.0f}m "


def render(m: dict, color: bool = True) -> str:
    red, grn, dim, off = (
        (_RED, _GRN, _DIM, _OFF) if color else ("", "", "", ""))
    out = [
        f"cascade dashboard  -  {time.strftime('%H:%M:%S')}  "
        f"|  {m['total_records']} records  |  {m['episodes']} episodes",
        f"{dim}source: runs/*.rec (recorder ground truth, not narration){off}",
        "",
        "MCP LIVENESS              calls   ok  err   succ%   last   "
        "p50ms  p95ms  maxms",
    ]
    for srv, s in m["per_server"].items():
        sp = f"{s['success_pct']:5.0f}" if s["success_pct"] is not None \
            else "  -- "
        out.append(
            f"  {srv:<20} {s['calls']:6d} {s['ok']:4d} {s['err']:4d}  "
            f"{sp}   {_age(s['age_s'])} "
            f"{s['p50_ms']:6.0f} {s['p95_ms']:6.0f} {s['max_ms']:6.0f}")

    p = m["producing"]
    cats = " ".join(f"{k}:{v}" for k, v in p["route_categories"].items()) \
        or "(none)"
    out += [
        "",
        "PRODUCING RESULTS",
        f"  drafts={p['drafts']}  truncated={p['trunc']} "
        f"({p['trunc_pct']:.0f}%)   route categories: {cats}",
    ]

    f = m["failures"]
    errs = " ".join(f"{k}:{v}" for k, v in f["tool_errors"].items()) \
        or "(none)"
    out += [
        "",
        "FAILURES",
        f"  gate fails  syntax={f['syntax_fail']}  "
        f"functional={f['func_fail']}   gpu-unavailable={f['gpu_unavailable']}",
        f"  tool errors: {errs}",
    ]

    e = m["escalations"]
    rh = " ".join(f"{k}r:{v}" for k, v in sorted(e["repair_round_hist"].items()))
    th = " ".join(f"{k}:{v}" for k, v in e["final_tier"].items())
    out += [
        "",
        "ESCALATIONS",
        f"  gpu.generate calls={e['gpu_generate_calls']}   "
        f"repair rounds/episode: {rh or '(none)'}   "
        f"cap-hits={e['cap_hits']}",
        f"  final tier: {th or '(none)'}",
    ]

    sp = m["spend"]
    tone = grn if sp["clean"] else red
    out += [
        "",
        f"{tone}SPEND   edge-cloud calls={sp['cloud_calls']}   "
        f"total=${sp['usd']:.2f}   "
        f"{'OK (local-first invariant holds)' if sp['clean'] else 'NONZERO !'}"
        f"{off}",
    ]
    return "\n".join(out)


# ---- loop -------------------------------------------------------------------

def _enable_vt() -> None:
    """Best-effort: turn on ANSI VT processing on legacy Windows consoles
    (Windows Terminal already supports it; this rescues plain conhost)."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        k.GetConsoleMode(h, ctypes.byref(mode))
        k.SetConsoleMode(h, mode.value | 0x0004)


def snapshot(gap: float) -> dict:
    return compute_metrics(tag_and_merge(load_streams(RUNS)), gap)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Live cascade health dashboard over runs/*.rec")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    metavar="SEC", help=f"refresh seconds (def {DEFAULT_INTERVAL:g})")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP, metavar="SEC",
                    help=f"episode idle-gap split (def {DEFAULT_GAP:g}s)")
    ap.add_argument("--once", action="store_true",
                    help="one snapshot then exit")
    ap.add_argument("--json", action="store_true",
                    help="machine snapshot (implies --once)")
    args = ap.parse_args()

    if args.json:
        print(json.dumps(snapshot(args.gap), indent=2))
        return
    if args.once:
        print(render(snapshot(args.gap), color=sys.stdout.isatty()))
        return

    _enable_vt()
    try:
        while True:
            frame = render(snapshot(args.gap))
            sys.stdout.write(CLEAR + frame +
                             f"\n\n(refresh {args.interval:g}s - Ctrl-C to exit)\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nbye.")


if __name__ == "__main__":
    main()
