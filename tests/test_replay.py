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


# ---- load_streams_incremental: offset tracking + rotation guard -------------

def _w(p, data: bytes) -> None:
    """Append-only write (mirrors the recorder; binary, no newline mangling)."""
    with open(p, "ab") as fh:
        fh.write(data)


def test_load_streams_incremental_first_call_reads_whole_file(tmp_path):
    p = tmp_path / "edge-npu.rec"
    _w(p, _stream(_tool("100.0"), _tool("110.0")))
    new = R.load_streams_incremental(tmp_path, {})
    assert "edge-npu" in new
    buf, start = new["edge-npu"]
    assert start == 0 and buf == p.read_bytes()


def test_load_streams_incremental_returns_only_new_bytes_on_growth(tmp_path):
    p = tmp_path / "edge-gpu.rec"
    initial = _stream(_tool("100.0"))
    _w(p, initial)
    offsets = {"edge-gpu": len(initial)}    # caller pretends it's parsed up to here
    # Append more (a second logical record).
    extra = _stream(_tool("200.0"))
    _w(p, extra)
    new = R.load_streams_incremental(tmp_path, offsets)
    buf, start = new["edge-gpu"]
    assert start == len(initial)
    assert buf == extra                     # ONLY the new bytes, not the whole file


def test_load_streams_incremental_no_growth_omits_stream(tmp_path):
    p = tmp_path / "edge-verify.rec"
    data = _stream(_tool("100.0"))
    _w(p, data)
    new = R.load_streams_incremental(tmp_path, {"edge-verify": len(data)})
    assert new == {}                        # nothing new since last call -> no entry


def test_load_streams_incremental_rotation_resets_offset(tmp_path):
    p = tmp_path / "edge-npu.rec"
    _w(p, _stream(_tool("100.0"), _tool("200.0")))
    big_offset = p.stat().st_size + 9999    # pretend caller tracked past EOF
    # Simulate rotation: rewrite the file fresh (shorter than the tracked offset).
    p.write_bytes(_stream(_tool("300.0")))
    new = R.load_streams_incremental(tmp_path, {"edge-npu": big_offset})
    buf, start = new["edge-npu"]
    assert start == 0                       # rotation -> read from offset 0
    assert buf == p.read_bytes()


def test_load_streams_incremental_skips_empty_files(tmp_path):
    (tmp_path / "edge-cloud.rec").write_bytes(b"")
    assert R.load_streams_incremental(tmp_path, {}) == {}
