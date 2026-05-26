/**
 * TS port of cascade/logfmt.py — bytes-native incremental parser for the
 * cascade's `.rec` record grammar.
 *
 * GRAMMAR (EBNF, UTF-8):
 *     stream = { record } ;
 *     record = begin , { field } , end ;
 *     begin  = "%%REC" , SP , "v" , uint , SP , uint , LF ;
 *     field  = key , SP , uint , LF , octet * uint , LF ;
 *     end    = "%%END" , LF ;
 *
 * Determinism rule: a field declares the exact byte length of its value, so
 * the value may contain LF / "%%REC" / "%%END" without ambiguity. Parsing is
 * O(n), total, no backtracking. A truncated trailing record is dropped (and
 * its byte offset returned as `nextOffset` so the next call retries it once
 * more bytes arrive).
 *
 * This module mirrors `cascade/logfmt.py::parse_stream_incremental` byte-for-
 * byte; see the Python source for the canonical semantics and the test suite
 * for the cross-check.
 */

const BEGIN = encodeAscii("%%REC ");
const END = encodeAscii("%%END");
const LF = 0x0a;
const SP = 0x20;
const DIGIT_0 = 0x30;
const DIGIT_9 = 0x39;
const TEXT_DECODER = new TextDecoder("utf-8", { fatal: false });

export function parseStreamIncremental(
  data: Uint8Array,
  startOffset: number = 0,
  keep?: ReadonlySet<string>,
): { records: Record<string, string>[]; nextOffset: number } {
  const records: Record<string, string>[] = [];
  let i = startOffset;
  let safe = startOffset;
  const n = data.length;

  while (i < n) {
    const recordStart = i;
    let nl = indexOfLF(data, i, n);
    if (nl === -1) {
      // incomplete header line at tail; `safe` stays at recordStart.
      break;
    }
    const line = data.subarray(i, nl);
    if (!startsWithBytes(line, BEGIN)) {
      i = nl + 1;
      safe = i; // garbage line consumed
      continue;
    }
    // Header is exactly three space-separated tokens: %%REC v<N> <seq>.
    // The split is over the already-known-short header line; allocation cost
    // is bounded by the header width, not the record value width.
    const headerParts = splitBytesBySpace(line);
    if (
      headerParts.length !== 3 ||
      headerParts[1] === undefined ||
      headerParts[1][0] !== /* 'v' */ 0x76 ||
      headerParts[2] === undefined
    ) {
      i = nl + 1;
      safe = i; // malformed begin treated as garbage
      continue;
    }
    const rec: Record<string, string> = {
      _seq: TEXT_DECODER.decode(headerParts[2]),
    };
    i = nl + 1;
    let truncated = false;
    let ok = true;
    while (true) {
      nl = indexOfLF(data, i, n);
      if (nl === -1) {
        truncated = true; // mid-record, no further LF yet
        break;
      }
      const head = data.subarray(i, nl);
      if (equalBytes(head, END)) {
        i = nl + 1;
        break;
      }
      const sp = lastIndexOfSpace(head);
      if (sp === -1 || !isDigitSpan(head, sp + 1, head.length)) {
        ok = false; // permanent: bad field header
        break;
      }
      const key = TEXT_DECODER.decode(head.subarray(0, sp));
      const length = parseDigits(head, sp + 1, head.length);
      const vstart = nl + 1;
      const vend = vstart + length;
      if (vend + 1 > n) {
        truncated = true; // declared length runs past current EOF
        break;
      }
      if (data[vend] !== LF) {
        ok = false; // permanent: full bytes present, terminator wrong
        break;
      }
      if (!keep || keep.has(key)) {
        rec[key] = TEXT_DECODER.decode(data.subarray(vstart, vend));
      }
      i = vend + 1;
    }
    if (truncated) {
      // rewind so the next call retries this record once data grows
      i = recordStart;
      break;
    }
    if (ok) {
      records.push(rec);
      safe = i; // past `%%END\n`
    }
    // else (permanent corruption): the outer-while garbage-line skip self-
    // corrects; `safe` advances one line at a time as those lines are consumed.
  }
  return { records, nextOffset: safe };
}

export function parseStream(
  data: Uint8Array,
  keep?: ReadonlySet<string>,
): Record<string, string>[] {
  return parseStreamIncremental(data, 0, keep).records;
}

/** Serialise one record back to wire bytes -- mirror of
 * `cascade.logfmt.dump_record`. The Python writer is the CANONICAL one used
 * by the cascade itself (mcp_servers/_rec.py); this TS writer exists for
 * offline tooling (seed_replay.ts, tests) where we want a writer that lives
 * in the same module as the parser it round-trips against. Field-order is
 * insertion order; keys must match the parser's accepted shape (no spaces,
 * no LF). */
export function dumpRecord(
  seq: number,
  fields: Record<string, string>,
): Uint8Array {
  const chunks: Uint8Array[] = [];
  chunks.push(ENCODER.encode(`%%REC v1 ${String(seq)}\n`));
  for (const [key, value] of Object.entries(fields)) {
    if (key === "" || /[ \n]/.test(key)) {
      throw new Error(`illegal field key: ${JSON.stringify(key)}`);
    }
    const body = ENCODER.encode(value);
    chunks.push(ENCODER.encode(`${key} ${String(body.length)}\n`));
    chunks.push(body);
    chunks.push(ENCODER.encode("\n"));
  }
  chunks.push(ENCODER.encode("%%END\n"));
  return concat(chunks);
}

const ENCODER = new TextEncoder();

function concat(chunks: Uint8Array[]): Uint8Array {
  let n = 0;
  for (const c of chunks) n += c.length;
  const out = new Uint8Array(n);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

function encodeAscii(s: string): Uint8Array {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

function indexOfLF(data: Uint8Array, from: number, to: number): number {
  for (let i = from; i < to; i++) {
    if (data[i] === LF) return i;
  }
  return -1;
}

function startsWithBytes(haystack: Uint8Array, needle: Uint8Array): boolean {
  if (haystack.length < needle.length) return false;
  for (let i = 0; i < needle.length; i++) {
    if (haystack[i] !== needle[i]) return false;
  }
  return true;
}

function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function splitBytesBySpace(data: Uint8Array): Uint8Array[] {
  const parts: Uint8Array[] = [];
  let start = 0;
  for (let i = 0; i < data.length; i++) {
    if (data[i] === SP) {
      parts.push(data.subarray(start, i));
      start = i + 1;
    }
  }
  parts.push(data.subarray(start));
  return parts;
}

function lastIndexOfSpace(data: Uint8Array): number {
  for (let i = data.length - 1; i >= 0; i--) {
    if (data[i] === SP) return i;
  }
  return -1;
}

function isDigitSpan(data: Uint8Array, from: number, to: number): boolean {
  if (from >= to) return false; // empty digit span is not numeric
  for (let i = from; i < to; i++) {
    const b = data[i];
    if (b === undefined || b < DIGIT_0 || b > DIGIT_9) return false;
  }
  return true;
}

function parseDigits(data: Uint8Array, from: number, to: number): number {
  let n = 0;
  for (let i = from; i < to; i++) {
    n = n * 10 + ((data[i] ?? 0) - DIGIT_0);
  }
  return n;
}
