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
        src: "".join(dump_record(i, r) for i, r in enumerate(recs))
        for src, recs in streams.items()
    })


def _t(tool: str, result: str, ok: str = "true", ts: str = "1.0") -> dict:
    return {"tool": tool, "ok": ok, "ts": ts, "run_id": "r",
            "latency_ms": "10.0", "result": result}


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


def test_render_is_total_and_marks_spend_state():
    clean = D.render(D.compute_metrics([]), color=False)
    assert "MCP LIVENESS" in clean and "SPEND" in clean
    assert "OK (local-first invariant holds)" in clean

    dirty = D.compute_metrics(_records(**{"edge-cloud": [
        _t("ask", '{"est_cost_usd": 1.5}')]}))
    assert "NONZERO !" in D.render(dirty, color=False)
