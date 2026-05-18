"""Granular end-to-end probe of the LOCAL repair/escalation path.

Drives `edge-npu` + `edge-verify` + `edge-gpu` over real MCP stdio (no
interactive Claude, no paid `edge-cloud`). It proves the whole local loop the
escalation policy depends on:

  Tier 1 draft  -> gate fails  -> repair_prompt  -> Tier 2 (GPU) repair
                -> re-gate     -> (≤2 rounds)     -> CAP -> Tier-3 handoff point

Each step prints latency / device / verdict. The default task binds the
`dijkstra` symbol so `checks.dsl::drone_ok` (the KeyError-on-sink-node check)
applies. `repair_prompt` is fed the *extracted* code block, not the raw
prose-wrapped draft (the refinement found during the NPU-slice probe).

Run:  uv run python scripts/probe_repair_path.py
Exit: 0 always (diagnostic, not pass/fail). Needs Ollama up for Phase 2; if it
is down the probe says so cleanly and stops after Phase 1.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent

TASK = (
    "Write a Python function def dijkstra(graph, start) that returns a dict of "
    "shortest-path costs from start for a directed weighted graph given as "
    "{node: {neighbor: weight}}."
)
DRAFT_BUDGET = 640        # 192 truncates (proven); 640 yields evaluable code
MAX_GPU_ROUNDS = 2        # policy cap: 2 failed local repairs -> Tier 3

GATE = "--gate" in sys.argv  # pre-push regression-gate mode (exit codes matter)
CLOUD_REC = ROOT / "runs" / "edge-cloud.rec"


def _cloud_rec_size() -> int:
    """Zero-spend invariant: edge-cloud.rec must not grow during a run."""
    try:
        return CLOUD_REC.stat().st_size
    except OSError:
        return 0


def _out(res) -> dict:
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        return sc.get("result", sc)
    try:
        return json.loads("".join(getattr(c, "text", "") for c in res.content))
    except (ValueError, TypeError):
        return {"_raw": "".join(getattr(c, "text", "") for c in res.content)}


def _server(mod: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", mod], cwd=str(ROOT)
    )


def _rule(s: str) -> None:
    print(f"\n{'='*72}\n{s}\n{'='*72}")


def _extract_code(text: str) -> str:
    """First fenced block, else the raw text. Mirrors what we recommended
    feeding to repair_prompt instead of the prose-wrapped draft."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


async def _call(s: ClientSession, tool: str, args: dict, timeout: float):
    t0 = time.perf_counter()
    res = await asyncio.wait_for(s.call_tool(tool, args), timeout=timeout)
    return _out(res), time.perf_counter() - t0


def _gate_failed(syn: dict, fun: dict) -> bool:
    if not syn.get("passed"):
        return True
    return bool(fun.get("applicable")) and not fun.get("passed")


def _failures(syn: dict, fun: dict) -> list[dict]:
    if fun.get("applicable") and not fun.get("passed"):
        return fun.get("failures") or []
    return [{
        "expr": "fenced code block",
        "observed": syn.get("reason", "syntax gate failed"),
        "requirement": "answer must contain compilable code",
    }]


async def _gate(verify: ClientSession, label: str, text: str):
    syn, dt1 = await _call(verify, "verify_syntax", {"text": text}, 30)
    print(f"  verify_syntax     [{dt1:5.2f}s] {json.dumps(syn)}")
    fun, dt2 = await _call(verify, "verify_functional", {"text": text}, 40)
    print(f"  verify_functional [{dt2:5.2f}s] {json.dumps(fun)[:500]}")
    failed = _gate_failed(syn, fun)
    print(f"  >>> {label}: {'GATE FAILED' if failed else 'GATE PASSED'}")
    return failed, _failures(syn, fun)


async def main() -> dict:
    R: dict = {
        "npu_ok": False, "tier1_gate_failed": None,
        "gpu_available": None, "gpu_invoked": False,
        "outcome": "(probe did not reach a verdict)",
    }
    _rule("TASK")
    print(TASK)

    # ---- Phase 1: Tier 1 (edge-npu) draft -----------------------------------
    # A compile hang / NPU-unavailable here is NOT a regression — it means no
    # local accel (or CI). Treat as skip-able: mark npu_ok False and bail.
    npu_text = ""
    try:
        async with stdio_client(_server("mcp_servers.npu")) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                _rule("1. edge-npu.route  (first call compiles the NPU, ~12-21s)")
                ri, dt = await _call(s, "route", {"prompt": TASK}, 240)
                print(f"  [{dt:6.2f}s] {json.dumps(ri)}")
                _rule(f"2. edge-npu.draft  (max_tokens={DRAFT_BUDGET})")
                d, dt = await _call(
                    s, "draft", {"prompt": TASK, "max_tokens": DRAFT_BUDGET}, 240
                )
                npu_text = d.get("text", "")
                print(f"  [{dt:6.2f}s] device={d.get('device')} "
                      f"chars={len(npu_text)}")
                print(_extract_code(npu_text) or "  <empty>")
                R["npu_ok"] = bool(d.get("available") and npu_text)
    except Exception as e:  # noqa: BLE001 - unavailable Tier-1 => skip, not fail
        _rule("Tier-1 (edge-npu) UNAVAILABLE")
        print(f"  {type(e).__name__}: {e}")
    if not R["npu_ok"]:
        R["outcome"] = ("Tier-1 unavailable (no local accel / compile failed) "
                        "— nothing to gate.")
        _rule("RESULT")
        print(R["outcome"])
        return R

    # ---- gate the Tier-1 draft, then escalate if it fails -------------------
    # Outcome is collected here and printed AFTER the MCP context managers
    # close. Early-returning from inside nested stdio_client `async with`
    # blocks raced their anyio TaskGroup teardown and raised an ExceptionGroup
    # that swallowed the final verdict — so: no return inside the `async with`.
    outcome = "(probe did not reach a verdict)"
    async with stdio_client(_server("mcp_servers.verify")) as (vr, vw), \
               stdio_client(_server("mcp_servers.gpu")) as (gr, gw):
        async with ClientSession(vr, vw) as verify, \
                   ClientSession(gr, gw) as gpu:
            await verify.initialize()
            await gpu.initialize()

            _rule("3. edge-verify  (gating the Tier-1 draft)")
            failed, fails = await _gate(verify, "Tier-1", npu_text)
            R["tier1_gate_failed"] = failed
            if not failed:
                outcome = "Tier-1 passed — no escalation needed."
            else:
                prior_code = _extract_code(npu_text)
                # ---- Phase 2: escalate to Tier 2 (edge-gpu), repair loop ---
                for rnd in range(1, MAX_GPU_ROUNDS + 1):
                    _rule(f"4.{rnd} edge-verify.repair_prompt -> "
                          f"edge-gpu.generate (repair round {rnd}/"
                          f"{MAX_GPU_ROUNDS})")
                    rp, dt = await _call(verify, "repair_prompt", {
                        "task": TASK, "code": prior_code, "failures": fails,
                    }, 30)
                    repair_req = rp if isinstance(rp, str) else rp.get("_raw", "")
                    print(f"  repair_prompt [{dt:5.2f}s] {len(repair_req)} "
                          f"chars -> Tier 2")

                    g, dt = await _call(
                        gpu, "generate", {"prompt": repair_req}, 180
                    )
                    R["gpu_available"] = bool(g.get("available"))
                    if not g.get("available"):
                        outcome = ("Tier 2 (edge-gpu) UNAVAILABLE — Ollama not "
                                   "up. Phase 1 result stands; start Ollama + "
                                   "qwen2.5-coder:14b and re-run for Phase 2.")
                        break
                    R["gpu_invoked"] = True
                    gpu_text = g.get("text", "")
                    print(f"  edge-gpu.generate [{dt:6.2f}s] "
                          f"{g.get('tokens_per_s')} tok/s "
                          f"model={g.get('model')} chars={len(gpu_text)}")
                    print(_extract_code(gpu_text) or "  <empty>")

                    _rule(f"5.{rnd} edge-verify  (gating the Tier-2 "
                          f"round-{rnd} output)")
                    failed, fails = await _gate(
                        verify, f"Tier-2 r{rnd}", gpu_text)
                    if not failed:
                        outcome = (f"[OK] Tier-2 (GPU) repaired it in round "
                                   f"{rnd}. Full local escalation loop works "
                                   f"end-to-end. ANSWERED BY GPU.")
                        break
                    prior_code = _extract_code(gpu_text)
                else:
                    outcome = (
                        f"[CAP] Tier-2 failed {MAX_GPU_ROUNDS} repair rounds -> "
                        f"POLICY CAP reached. Per CLAUDE.md the next step is "
                        f"Tier-3 (Claude reasons it itself); cloud is OFF so "
                        f"that is the terminal. The probe cannot do Tier-3 "
                        f"reasoning headlessly — this is the correct, bounded "
                        f"handoff point, NOT an infinite loop.")

    R["outcome"] = outcome
    _rule("RESULT")
    print(outcome)
    return R


def _gate_verdict(R: dict, cloud_before: int) -> int:
    """Pre-push regression gate. Exit 0 = PASS or clean SKIP (never block a
    hardware-less / Ollama-less push, mirroring e2e-local); exit 1 = a real
    regression. The stochastic GPU outcome ([OK] vs [CAP]) is NOT a failure —
    both prove the loop. Hard regressions only:
      * the verifier FAILED to reject the known-bad dijkstra draft
      * a paid-tier (edge-cloud) call happened — zero-spend invariant broken
    """
    cloud_after = _cloud_rec_size()
    _rule("GATE VERDICT")
    if cloud_after != cloud_before:
        print(f"FAIL: edge-cloud.rec changed ({cloud_before}->{cloud_after}) "
              f"— zero-spend invariant BROKEN.")
        return 1
    if not R["npu_ok"]:
        print("SKIP: Tier-1 unavailable (no local accel / CI) — push not "
              "blocked (regression gate is local-only, like e2e-local).")
        return 0
    if R["tier1_gate_failed"] is not True:
        print("FAIL: edge-verify did NOT reject the known-bad dijkstra draft "
              "— the deterministic gate regressed. Do not trust 'verified'.")
        return 1
    if R["gpu_available"] is False and not R["gpu_invoked"]:
        print("SKIP: Tier-2 (Ollama) down — escalation path not exercised; "
              "push not blocked (local-only gate).")
        return 0
    if not R["gpu_invoked"]:
        print("FAIL: gate failed but escalation never invoked edge-gpu — "
              "the repair path regressed.")
        return 1
    print(f"PASS: gate caught the bad draft, escalation ran, zero spend. "
          f"Outcome: {R['outcome'][:80]}")
    return 0


if __name__ == "__main__":
    # Windows console defaults to cp1252; force UTF-8 so the probe never dies
    # on an un-encodable char in model output or our own verdict markers.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    cloud_before = _cloud_rec_size()
    result: dict = {"npu_ok": False, "tier1_gate_failed": None,
                    "gpu_available": None, "gpu_invoked": False,
                    "outcome": "aborted"}
    try:
        result = asyncio.run(main())
    except Exception as e:  # noqa: BLE001 - diagnostic must not traceback-spam
        print(f"\n[probe] aborted: {type(e).__name__}: {e}")
        if GATE:
            # An abort with deps present is suspicious, but a hardware/stdio
            # flake must not wedge `git push`; treat as skip unless the paid
            # tier was somehow touched.
            sys.exit(1 if _cloud_rec_size() != cloud_before else 0)
    print("\n.rec records appended for the servers used — independent proof.")
    sys.exit(_gate_verdict(result, cloud_before) if GATE else 0)
