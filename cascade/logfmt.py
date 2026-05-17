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

Records are append-only; a truncated trailing record (writer crashed mid-emit)
is dropped by the parser, never partially yielded.
"""
from __future__ import annotations

VERSION = 1
_BEGIN = "%%REC"
_END = "%%END"


def dump_record(seq: int, fields: dict[str, str]) -> str:
    """Serialise one record. Field order is preserved (insertion order); keys
    must match /[A-Za-z_][A-Za-z0-9_]*/ and contain no space (they label a
    length-framed value, so the value itself is unconstrained)."""
    out = [f"{_BEGIN} v{VERSION} {seq}\n"]
    for key, value in fields.items():
        if not key or " " in key or "\n" in key:
            raise ValueError(f"illegal field key: {key!r}")
        body = value.encode("utf-8")
        out.append(f"{key} {len(body)}\n")
        out.append(value)
        out.append("\n")
    out.append(f"{_END}\n")
    return "".join(out)


def parse_stream(text: str) -> list[dict[str, str]]:
    """Tokenise a record stream deterministically. Unknown/garbage lines
    between records are skipped; an incomplete trailing record is dropped.
    Returns each record as an insertion-ordered {key: value} dict (plus
    "_seq": str)."""
    data = text.encode("utf-8")
    records: list[dict[str, str]] = []
    i = 0
    n = len(data)
    while i < n:
        nl = data.find(b"\n", i)
        if nl == -1:
            break
        line = data[i:nl].decode("utf-8", "replace")
        if not line.startswith(_BEGIN + " "):
            i = nl + 1  # not a record start -- skip this line
            continue
        parts = line.split(" ")
        # %%REC vN SEQ  -> exactly 3 tokens; malformed header => skip the line
        if len(parts) != 3 or not parts[1].startswith("v"):
            i = nl + 1
            continue
        rec: dict[str, str] = {"_seq": parts[2]}
        i = nl + 1
        ok = True
        while True:
            nl = data.find(b"\n", i)
            if nl == -1:
                ok = False  # truncated mid-record -> drop it
                break
            head = data[i:nl].decode("utf-8", "replace")
            if head == _END:
                i = nl + 1
                break
            sp = head.rfind(" ")
            if sp == -1 or not head[sp + 1:].isdigit():
                ok = False
                break
            key, length = head[:sp], int(head[sp + 1:])
            vstart = nl + 1
            vend = vstart + length
            if vend + 1 > n or data[vend:vend + 1] != b"\n":
                ok = False  # declared length runs past EOF / no LF terminator
                break
            rec[key] = data[vstart:vend].decode("utf-8", "replace")
            i = vend + 1
        if ok:
            records.append(rec)
    return records
