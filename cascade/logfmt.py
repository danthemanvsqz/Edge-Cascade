"""Deterministic record grammar for the cascade's non-deterministic logs.

The human tee log (runs/cascade.log) is for eyeballs / `tail -f` and is NOT
safely parseable: a model answer that emits a line looking like a timestamp
breaks the regex scraper. This module defines a SECOND, structured stream
(runs/cascade.rec) whose grammar frames every variable-length, untrusted
payload by BYTE LENGTH, so the tokenizer never has to guess where a value
ends -- parsing is fully deterministic regardless of what the model emitted.

GRAMMAR  (the whole format -- EBNF; encoding = UTF-8)
----------------------------------------------------------------------------
    stream   = { record } ;
    record   = begin , { field } , end ;
    begin    = "%%REC" , SP , "v" , uint , SP , uint , LF ;  (* version, seq *)
    field    = key , SP , uint , LF , octet * uint , LF ;    (* uint = #bytes *)
    end      = "%%END" , LF ;
    key      = ( ALPHA | "_" ) , { ALPHA | DIGIT | "_" } ;
    uint     = DIGIT , { DIGIT } ;
    SP = %x20 ; LF = %x0A ;

Determinism rule: a `field` declares the exact byte length of its value, so
the reader consumes precisely that many bytes -- the value may contain LF,
"%%REC", "%%END", fake "12:34:56" timestamps, anything -- without ambiguity.
The only structural tokens are the line-oriented `begin`/`end`/key headers,
and a value can never be mistaken for one because its span is counted, not
delimited. Tokenizing + parsing is therefore O(n) and total (no backtracking,
no regex, no escaping).

The format is byte-framed, so this module is bytes-native: `dump_record`
returns `bytes` (encode the value once) and `parse_stream` consumes `bytes`
(no decode->re-encode round trip on the caller's file read). `parse_stream`
takes an optional `keep` projection: an unwanted field's value is skipped by
advancing the counted span WITHOUT decoding/materialising it -- but its length
header and LF terminator are still validated, so a malformed skipped field can
never desync the stream (the determinism guarantee holds under projection).

Records are append-only; a truncated trailing record (writer crashed mid-emit)
is dropped by the parser, never partially yielded.
"""
from __future__ import annotations

VERSION = 1
_BEGIN = b"%%REC"
_END = b"%%END"


def dump_record(seq: int, fields: dict[str, str]) -> bytes:
    """Serialise one record to bytes. Field order is preserved (insertion
    order); keys must match /[A-Za-z_][A-Za-z0-9_]*/ and contain no space (they
    label a length-framed value, so the value itself is unconstrained). Each
    value is UTF-8 encoded exactly once."""
    out = bytearray(b"%s v%d %d\n" % (_BEGIN, VERSION, seq))
    for key, value in fields.items():
        if not key or " " in key or "\n" in key:
            raise ValueError(f"illegal field key: {key!r}")
        body = value.encode("utf-8")
        out += b"%s %d\n" % (key.encode("utf-8"), len(body))
        out += body
        out += b"\n"
    out += _END + b"\n"
    return bytes(out)


def parse_stream(
    data: bytes, keep: frozenset[str] | None = None
) -> list[dict[str, str]]:
    """Tokenise a record stream deterministically. Unknown/garbage lines
    between records are skipped; an incomplete trailing record is dropped.
    Returns each record as an insertion-ordered {key: value} dict (plus
    "_seq": str).

    `keep`: when given, only fields whose key is in the set are decoded and
    stored; other fields are skipped by advancing past their counted value
    (no decode, no allocation) -- their length header and LF terminator are
    still validated, so projection cannot desync the stream. "_seq" is always
    present (it is header-derived, not a field)."""
    records: list[dict[str, str]] = []
    i = 0
    n = len(data)
    while i < n:
        nl = data.find(b"\n", i)
        if nl == -1:
            break
        line = data[i:nl]
        if not line.startswith(_BEGIN + b" "):
            i = nl + 1  # not a record start -- skip this line
            continue
        parts = line.split(b" ")
        # %%REC vN SEQ  -> exactly 3 tokens; malformed header => skip the line
        if len(parts) != 3 or not parts[1].startswith(b"v"):
            i = nl + 1
            continue
        rec: dict[str, str] = {"_seq": parts[2].decode("utf-8", "replace")}
        i = nl + 1
        ok = True
        while True:
            nl = data.find(b"\n", i)
            if nl == -1:
                ok = False  # truncated mid-record -> drop it
                break
            head = data[i:nl]
            if head == _END:
                i = nl + 1
                break
            sp = head.rfind(b" ")
            if sp == -1 or not head[sp + 1:].isdigit():
                ok = False
                break
            key = head[:sp].decode("utf-8", "replace")
            length = int(head[sp + 1:])
            vstart = nl + 1
            vend = vstart + length
            if vend + 1 > n or data[vend:vend + 1] != b"\n":
                ok = False  # declared length runs past EOF / no LF terminator
                break
            if keep is None or key in keep:
                rec[key] = data[vstart:vend].decode("utf-8", "replace")
            i = vend + 1
        if ok:
            records.append(rec)
    return records
