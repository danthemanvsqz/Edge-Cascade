"""logfmt is the deterministic record grammar. The load-bearing test is the
adversarial round-trip: a payload that embeds the grammar's own structural
tokens, fake timestamps, and newlines must parse back byte-identical -- that
is the property the human tee log lacks. Plus full branch coverage (this
module is inside the 100% gate)."""
import pytest

from cascade.logfmt import dump_record, parse_stream

# A model answer engineered to break a delimiter/regex parser: it contains the
# begin/end sentinels, a fake timestamp line, blank lines, and unicode.
ADVERSARIAL = (
    "```python\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "```\n"
    "%%END\n"                       # the end sentinel, verbatim, in the value
    "%%REC v1 999\n"                # a fake record header in the value
    "12:34:56 ---- QUERY: not a real record\n"
    "\n\ntrailing blank lines + unicode ✓ café\n"
)


def test_adversarial_round_trip_is_byte_identical():
    stream = dump_record(0, {"query": "reverse a string", "answer": ADVERSARIAL})
    recs = parse_stream(stream)
    assert len(recs) == 1
    assert recs[0]["_seq"] == "0"
    assert recs[0]["query"] == "reverse a string"
    # The whole point: embedded sentinels/timestamps/newlines survive exactly.
    assert recs[0]["answer"] == ADVERSARIAL


def test_multiple_records_and_noise_between_is_skipped():
    s = (
        dump_record(0, {"q": "one"})
        + "12:00:00 some human tee-log line that is not a record\n"
        + dump_record(1, {"q": "two"})
    )
    recs = parse_stream(s)
    assert [(r["_seq"], r["q"]) for r in recs] == [("0", "one"), ("1", "two")]


def test_field_order_preserved():
    recs = parse_stream(dump_record(7, {"b": "1", "a": "2", "c": "3"}))
    assert list(recs[0].keys()) == ["_seq", "b", "a", "c"]


@pytest.mark.parametrize("bad", ["", "has space", "has\nnewline"])
def test_dump_rejects_illegal_keys(bad):
    with pytest.raises(ValueError):
        dump_record(0, {bad: "v"})


@pytest.mark.parametrize("text", ["", "%%REC v1 0", "not a record at all"])
def test_no_complete_record_returns_empty(text):
    assert parse_stream(text) == []


@pytest.mark.parametrize(
    "header",
    ["%%REC v1 0 extra\n",   # len(parts) != 3  (left side of the OR)
     "%%REC x1 0\n"],        # parts[1] not 'v…' (right side of the OR)
)
def test_malformed_begin_header_skipped(header):
    # Header is skipped; a well-formed record after it still parses.
    recs = parse_stream(header + dump_record(5, {"q": "ok"}))
    assert len(recs) == 1 and recs[0]["_seq"] == "5"


def test_truncated_trailing_record_is_dropped():
    good = dump_record(0, {"q": "kept"})
    truncated = "%%REC v1 1\nq 10\nshort\n"          # field LF then EOF
    recs = parse_stream(good + truncated)
    assert [r["_seq"] for r in recs] == ["0"]


def test_record_truncated_immediately_after_begin_is_dropped():
    # Valid begin line + LF, then EOF before any field/%%END: the field loop
    # finds no further newline (nl == -1) and the record is dropped.
    recs = parse_stream(dump_record(0, {"q": "kept"}) + "%%REC v1 9\n")
    assert [r["_seq"] for r in recs] == ["0"]


def test_field_header_without_space_is_dropped():
    recs = parse_stream("%%REC v1 0\nnosizetoken\n%%END\n")
    assert recs == []


def test_field_header_non_numeric_length_is_dropped():
    recs = parse_stream("%%REC v1 0\nq notanumber\nabc\n%%END\n")
    assert recs == []


def test_declared_length_past_eof_is_dropped():
    recs = parse_stream("%%REC v1 0\nq 500\nshort\n")     # vend+1 > n
    assert recs == []


def test_value_length_ok_but_missing_lf_terminator_is_dropped():
    # length fits within the buffer but the byte after the value is not LF
    recs = parse_stream("%%REC v1 0\nq 3\nabcX%%END\n")
    assert recs == []
