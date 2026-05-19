"""replay.py is a read-only timeline reconstructor. Its load-bearing claims:
(1) records from independent per-server files merge into ONE wall-clock order;
(2) records written before the ts/run_id recorder change still sort sanely
(legacy fallback, not silent mis-ordering); (3) episode splitting matches the
documented heuristic -- idle-gap on the agentic stream, but every cascade.rec
record is an exact, isolated task boundary. All pure, synthetic .rec strings
(built via the real dump_record), no hardware."""
import replay as R
from cascade.logfmt import dump_record


def _stream(*recs: dict) -> bytes:
    """Serialise records into one .rec stream (the real grammar, bytes)."""
    return b"".join(dump_record(i, r) for i, r in enumerate(recs))


def _tool(ts: str | None, run: str = "r1", tool: str = "draft") -> dict:
    r = {"server": "x", "tool": tool, "run_id": run, "ok": "true"}
    if ts is not None:
        r["ts"] = ts
    return r


# ---- merge + order ----------------------------------------------------------

def test_merge_orders_across_servers_by_ts_not_by_file():
    streams = {
        "edge-npu": _stream(_tool("100.0", tool="route"),
                            _tool("300.0", tool="draft")),
        "edge-gpu": _stream(_tool("200.0", tool="generate")),
    }
    seq = [(r["_src"], r["tool"]) for r in R.tag_and_merge(streams)]
    assert seq == [("edge-npu", "route"),
                   ("edge-gpu", "generate"),
                   ("edge-npu", "draft")]


def test_legacy_untimed_records_sort_first_as_oldest_in_file_order():
    # Pre-ts records predate the recorder change -> they ARE the oldest, so
    # they sort BEFORE timed ones. This is what makes `--last N` return the
    # genuinely most-recent activity instead of a stale legacy lump.
    streams = {
        "edge-npu": _stream(_tool(None, tool="old_a"), _tool(None, tool="old_b")),
        "edge-gpu": _stream(_tool("500.0", tool="new")),
    }
    merged = R.tag_and_merge(streams)
    assert [r["tool"] for r in merged] == ["old_a", "old_b", "new"]
    assert R._ts(merged[0]) is None and R._ts(merged[-1]) == 500.0


# ---- episode splitting (the documented heuristic) ---------------------------

def test_idle_gap_splits_episodes_within_gap_does_not():
    recs = R.tag_and_merge({"edge-npu": _stream(
        _tool("100.0"), _tool("110.0"),          # +10s  -> same episode
        _tool("200.0"), _tool("205.0"))})         # +90s gap -> new episode
    eps = R.split_episodes(recs, gap=30.0)
    assert [len(e) for e in eps] == [2, 2]


def test_cascade_record_is_always_its_own_episode():
    # Two cli.py records 1s apart: gap heuristic would merge them, but
    # cascade.rec carries exact task boundaries, so each is isolated.
    cascade = _stream(
        {"ts": "100.0", "run_id": "c", "query": "q1",
         "final_tier": "npu", "total_latency_s": "1.0", "trace": "t"},
        {"ts": "101.0", "run_id": "c", "query": "q2",
         "final_tier": "gpu", "total_latency_s": "2.0", "trace": "t"})
    recs = R.tag_and_merge({"cascade": cascade})
    eps = R.split_episodes(recs, gap=30.0)
    assert len(eps) == 2 and all(len(e) == 1 for e in eps)


def test_legacy_run_id_change_and_legacy_to_timed_seam_split():
    recs = R.tag_and_merge({"edge-npu": _stream(
        _tool("100.0", run="A"),                  # timed (sorts last)
        _tool(None, run="B"), _tool(None, run="B"),   # legacy run B
        _tool(None, run="C"))})                       # legacy run C
    eps = R.split_episodes(recs, gap=30.0)
    # legacy-B | legacy-C | timed-A  (run_id change split + legacy->timed seam)
    assert [len(e) for e in eps] == [2, 1, 1]


# ---- failure classification (drives --failures-only) ------------------------

def test_is_failure_covers_tool_error_and_failed_gate_only():
    err = {"server": "x", "tool": "draft", "ok": "false", "error": "Boom: x"}
    gate_fail = {"server": "x", "tool": "verify_functional", "ok": "true",
                 "result": '{"passed": false}'}
    gate_ok = {"server": "x", "tool": "verify_functional", "ok": "true",
               "result": '{"passed": true}'}
    plain_ok = {"server": "x", "tool": "draft", "ok": "true",
                "result": '{"available": true}'}
    assert R.is_failure(err) is True
    assert R.is_failure(gate_fail) is True
    assert R.is_failure(gate_ok) is False
    assert R.is_failure(plain_ok) is False
