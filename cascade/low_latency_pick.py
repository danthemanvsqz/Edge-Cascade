"""Pure gating decision for the low_latency chord callback.

Extracted from cascade.topologies_canvas._pick_first_verified so the logic
falls under the 100% coverage gate. topologies_canvas is in coverage.omit
(Celery substrate, live-validated); this helper has no broker dependency and
is directly unit-testable with an injectable gate_fn.
"""
from __future__ import annotations

from collections.abc import Callable


def _pick_decision(
    results: list[dict],
    env: dict,
    gate_fn: Callable[[str, str | None], tuple[bool, list]],
) -> dict:
    """Gate raced low_latency candidates cheapest-first (npu before gpu).

    Skips unavailable or empty arms. Resolves to the first verified candidate.
    Sets capped=True on double-miss. Mutates and returns env.

    gate_fn(text, dsl) -> (passed: bool, failures: list) -- the same contract
    as cascade.topologies_canvas._gate; injectable so tests need no broker.
    """
    draft_res = results[0] if len(results) > 0 else {}
    gpu_res = results[1] if len(results) > 1 else {}
    for tier, res in (("npu", draft_res), ("gpu", gpu_res)):
        text = res.get("text", "") if isinstance(res, dict) else ""
        if not (isinstance(res, dict) and res.get("available", True) and text):
            env["trace"].append(f"low_latency: {tier} race candidate unavailable")
            continue
        passed, _ = gate_fn(text, env["dsl"])
        env["trace"].append(
            f"low_latency: {tier} race candidate gate "
            f"{'PASS' if passed else 'FAIL'}"
        )
        if passed:
            env["answer"] = text
            env["final_tier"] = tier
            env["resolved"] = True
            return env
    env["capped"] = True
    env["trace"].append(
        "low_latency: neither raced candidate verified -> capped->tier3"
    )
    return env
