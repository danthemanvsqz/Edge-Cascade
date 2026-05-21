"""dashboard.py's load-bearing core is compute_metrics: a pure, total
records -> metrics map. The tests pin the panels that matter operationally --
the SPEND invariant (must read clean at $0/0 and flip the instant a cloud
call appears), failure/escalation counting, draft-truncation detection, and
percentile math -- plus a render() smoke so a metrics dict always produces a
screen. Synthetic .rec via the real dump_record; no hardware."""
import dashboard as D
import replay as R
from cascade.logfmt import dump_record


def _records(**streams: list[dict]) -> list[dict]:
    """Build {src: .rec text} from dicts and run the real merge layer, so
    compute_metrics sees exactly what the live tool sees."""
    return R.tag_and_merge({
        src: b"".join(dump_record(i, r) for i, r in enumerate(recs))
        for src, recs in streams.items()
    })


def _t(tool: str, result: str, ok: str = "true", ts: str = "1.0",
       run_id: str = "r", latency_ms: str = "10.0") -> dict:
    return {"tool": tool, "ok": ok, "ts": ts, "run_id": run_id,
            "latency_ms": latency_ms, "result": result}


# ---- the spend invariant (the headline) -------------------------------------

def test_spend_reads_clean_with_no_cloud_activity():
    m = D.compute_metrics(_records(**{"edge-npu": [_t("draft", '{"a":1}')]}))
    assert m["spend"] == {"cloud_calls": 0, "usd": 0.0, "clean": True}


def test_spend_flips_dirty_the_instant_cloud_is_called():
    m = D.compute_metrics(_records(**{"edge-cloud": [
        _t("ask", '{"est_cost_usd": 0.0123}')]}))
    assert m["spend"]["cloud_calls"] == 1
    assert m["spend"]["usd"] == 0.0123
    assert m["spend"]["clean"] is False


# ---- producing / failures / escalations -------------------------------------

def test_full_local_loop_is_counted_correctly():
    # route -> draft -> syntax ok -> functional FAIL -> repair -> gpu.generate
    # -> functional PASS  == one escalated episode answered by GPU.
    npu = [_t("route", '{"category": "standard"}'), _t("draft", '{"text":"x"}')]
    verify = [_t("verify_syntax", '{"passed": true, "has_code": true}'),
              _t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"prompt text"'),
              _t("verify_syntax", '{"passed": true, "has_code": true}'),
              _t("verify_functional", '{"passed": true, "applicable": true}')]
    gpu = [_t("generate", '{"available": true, "text": "y"}')]
    m = D.compute_metrics(_records(**{
        "edge-npu": npu, "edge-verify": verify, "edge-gpu": gpu}), gap=300.0)
    assert m["producing"]["drafts"] == 1
    assert m["producing"]["route_categories"] == {"standard": 1}
    assert m["failures"]["func_fail"] == 1
    assert m["escalations"]["gpu_generate_calls"] == 1
    assert m["escalations"]["final_tier"] == {"gpu": 1}
    assert m["spend"]["clean"] is True


def test_cap_hit_when_two_repair_rounds_still_fail():
    verify = [_t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p1"'),
              _t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p2"'),
              _t("verify_functional", '{"passed": false, "applicable": true}')]
    m = D.compute_metrics(_records(**{"edge-verify": verify}), gap=300.0)
    assert m["escalations"]["cap_hits"] == 1
    assert m["escalations"]["final_tier"] == {"capped->tier3": 1}
    assert m["escalations"]["repair_round_hist"] == {2: 1}
    # 2 rounds is EXACTLY at the cap (REPAIR_CAP_MAX=2), not over.
    assert m["escalations"]["over_cap_episodes"] == 0
    # Cap-hit IS a tier3 takeover (the operationally interesting handoff).
    assert m["escalations"]["gpu_unavailable_episodes"] == 0
    assert m["escalations"]["tier3_takeovers"] == 1


def test_stray_records_are_fragments_not_unresolved():
    # A lone GPU generate with no gate is a 30s-split artifact, not a real solve
    # attempt -- it must NOT inflate "unresolved" (the 2026-05-20 finding: the
    # 7 "unresolved" episodes were all tiny fragments).
    m = D.compute_metrics(_records(**{
        "edge-gpu": [_t("generate", '{"available": true, "text": "y"}')]}),
        gap=300.0)
    assert m["escalations"]["final_tier"] == {"fragment": 1}


def test_gated_attempt_that_never_passes_stays_unresolved():
    # A real attempt (a gate ran) that never passed and didn't hit the cap is
    # genuinely unresolved -- still counted as such, not reclassified.
    verify = [_t("verify_syntax", '{"passed": true, "has_code": true}'),
              _t("verify_functional", '{"passed": false, "applicable": true}')]
    m = D.compute_metrics(_records(**{"edge-verify": verify}), gap=300.0)
    assert m["escalations"]["final_tier"] == {"unresolved": 1}


def test_over_cap_episodes_visible_when_loop_passes_after_3_rounds():
    # The policy breach today's dashboard could not see: rounds=3 but the
    # loop eventually passed -> _final_tier returns "gpu", cap_hits=0 (only
    # flags loops that ALSO failed). Without over_cap_episodes a 3-round
    # "GPU succeeded" episode looks healthy, hiding the violation.
    verify = [_t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p1"'),
              _t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p2"'),
              _t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p3"'),                 # round 3 == breach
              _t("verify_functional", '{"passed": true, "applicable": true}')]
    gpu = [_t("generate", '{"available": true, "text": "y"}')]
    m = D.compute_metrics(
        _records(**{"edge-verify": verify, "edge-gpu": gpu}), gap=300.0)
    assert m["escalations"]["over_cap_episodes"] == 1
    assert m["escalations"]["cap_hits"] == 0                   # NOT a cap-hit
    assert m["escalations"]["final_tier"] == {"gpu": 1}        # still "gpu"
    assert m["escalations"]["repair_round_hist"] == {3: 1}


def test_gpu_unavailable_episode_counts_as_tier3_takeover():
    # GPU was reached but reported `available:false` (Ollama down /
    # model missing) -- still a Claude-takes-over event, but a different
    # flavor than the loop-exhaustion cap_hit case.
    gpu = [_t("generate", '{"available": false, "text": "[gpu unavailable]"}')]
    m = D.compute_metrics(_records(**{"edge-gpu": gpu}), gap=300.0)
    e = m["escalations"]
    assert e["gpu_unavailable_episodes"] == 1
    assert e["cap_hits"] == 0
    assert e["tier3_takeovers"] == 1                # aggregate counts both
    # Per-record count is still maintained for drill-down.
    assert m["failures"]["gpu_unavailable"] == 1


def test_takeover_aggregate_sums_both_flavors():
    # Episode A: 2-round loop exhaustion -> cap_hit takeover.
    # Episode B: gpu-unavailable -> different-flavor takeover.
    # tier3_takeovers should be 2.
    ep_a_verify = [_t("verify_functional", '{"passed": false, "applicable": true}', ts="100.0"),
                   _t("repair_prompt", '"p1"', ts="101.0"),
                   _t("verify_functional", '{"passed": false, "applicable": true}', ts="102.0"),
                   _t("repair_prompt", '"p2"', ts="103.0"),
                   _t("verify_functional", '{"passed": false, "applicable": true}', ts="104.0")]
    ep_b_gpu = [_t("generate", '{"available": false}', ts="9999.0")]   # far gap
    m = D.compute_metrics(
        _records(**{"edge-verify": ep_a_verify, "edge-gpu": ep_b_gpu}),
        gap=30.0,
    )
    e = m["escalations"]
    assert e["cap_hits"] == 1
    assert e["gpu_unavailable_episodes"] == 1
    assert e["tier3_takeovers"] == 2


def test_truncated_draft_and_tool_errors_are_detected():
    verify = [_t("verify_syntax",
                 '{"passed": false, "has_code": false}')]      # truncation
    npu = [_t("draft", "", ok="false")]
    npu[0]["error"] = "TimeoutError: NPU stalled"
    m = D.compute_metrics(_records(**{"edge-verify": verify, "edge-npu": npu}))
    assert m["producing"]["trunc"] == 1
    assert m["producing"]["trunc_pct"] == 100.0
    assert m["failures"]["syntax_fail"] == 1
    assert m["failures"]["tool_errors"] == {"TimeoutError": 1}
    assert D.compute_metrics([])["per_server"]["edge-npu"]["calls"] == 0


# ---- pure numeric + render --------------------------------------------------

def test_pctl_is_total_and_nearest_rank():
    assert D._pctl([], 95) == 0.0                       # total on empty
    assert D._pctl([42.0], 50) == 42.0                  # single value
    assert D._pctl([10, 20, 30, 40, 50], 50) == 30      # midpoint, odd n
    assert D._pctl([1, 2, 3, 4], 0) == 1                # 0th = min
    assert D._pctl([1, 2, 3, 4], 100) == 4              # 100th = max
    assert D._pctl([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10.0


def test_per_server_separates_cold_from_steady_per_run_id():
    # Two NPU run_ids, each with a big first-call (the vpux compile / model
    # load) + small subsequent drafts. Steady percentiles must reflect the
    # small ones; cold_max must surface the compile.
    npu = [
        _t("route", "{}", ts="100.0", run_id="A", latency_ms="20000.0"),  # cold A
        _t("draft", "{}", ts="101.0", run_id="A", latency_ms="500.0"),
        _t("draft", "{}", ts="102.0", run_id="A", latency_ms="600.0"),
        _t("route", "{}", ts="200.0", run_id="B", latency_ms="18000.0"),  # cold B
        _t("draft", "{}", ts="201.0", run_id="B", latency_ms="700.0"),
    ]
    s = D.compute_metrics(_records(**{"edge-npu": npu}))["per_server"]["edge-npu"]
    # Steady = 500, 600, 700 -> max=700, p95=700.
    assert s["max_ms"] == 700.0
    assert s["p95_ms"] == 700.0
    # Cold pool = {18000, 20000}; the worst observed compile is the headline.
    assert s["cold_max_ms"] == 20000.0


def test_status_calls_excluded_from_latency():
    # `status` is a cheap probe, NOT generation work; counting it as the cold
    # call would mask the real compile in the panel.
    npu = [
        _t("status", "{}", ts="100.0", run_id="A", latency_ms="5.0"),      # ignored
        _t("route", "{}", ts="101.0", run_id="A", latency_ms="20000.0"),   # cold
        _t("draft", "{}", ts="102.0", run_id="A", latency_ms="500.0"),     # steady
    ]
    s = D.compute_metrics(_records(**{"edge-npu": npu}))["per_server"]["edge-npu"]
    assert s["cold_max_ms"] == 20000.0   # the route, not the 5ms status
    assert s["p50_ms"] == 500.0          # the draft, not the 5ms status


def test_render_is_total_and_marks_spend_state():
    clean = D.render(D.compute_metrics([]), color=False)
    assert "MCP LIVENESS" in clean and "SPEND" in clean
    assert "OK (local-first invariant holds)" in clean
    # over_cap=0 path: no ANSI escape, just plain text.
    assert "over-cap=0" in clean and "\x1b[31m" not in clean

    dirty = D.compute_metrics(_records(**{"edge-cloud": [
        _t("ask", '{"est_cost_usd": 1.5}')]}))
    assert "NONZERO !" in D.render(dirty, color=False)


def test_render_surfaces_tier3_takeovers_line():
    # The "GPU couldn't, Claude took over" question must read at a glance.
    gpu = [_t("generate", '{"available": false}')]
    rendered = D.render(D.compute_metrics(_records(**{"edge-gpu": gpu})),
                        color=False)
    assert "gpu→tier3 takeovers=1" in rendered
    assert "cap_hits=0" in rendered                          # drill-down
    assert "gpu_unavailable_episodes=1" in rendered          # drill-down


def test_render_marks_over_cap_red_when_breached():
    # Same 3-round-and-passes pattern as the metric test; the render must
    # surface the policy breach unmistakably (red when color is on).
    verify = [_t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p1"'),
              _t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p2"'),
              _t("verify_functional", '{"passed": false, "applicable": true}'),
              _t("repair_prompt", '"p3"'),
              _t("verify_functional", '{"passed": true, "applicable": true}')]
    gpu = [_t("generate", '{"available": true, "text": "y"}')]
    m = D.compute_metrics(
        _records(**{"edge-verify": verify, "edge-gpu": gpu}), gap=300.0)
    colored = D.render(m, color=True)
    assert f"{D._RED}over-cap=1{D._OFF}" in colored
    plain = D.render(m, color=False)
    assert "over-cap=1" in plain and "\x1b[31m" not in plain
