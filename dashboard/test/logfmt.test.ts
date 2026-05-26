import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { parseStream, parseStreamIncremental } from "../src/lib/logfmt.js";

const FIXTURE_DIR = join(dirname(fileURLToPath(import.meta.url)), "fixtures");
const ENCODER = new TextEncoder();

/** Mirror of `cascade.logfmt.dump_record` — only used by the synthetic tests
 * below. Throws on keys that the parser would reject (whitespace), so a test
 * cannot accidentally fabricate a byte sequence the canonical parser would
 * never produce. */
function dumpRecord(seq: number, fields: Record<string, string>): Uint8Array {
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

describe("parseStream", () => {
  it("round-trips a single well-formed record", () => {
    const bytes = dumpRecord(0, { server: "edge-gpu", tool: "generate" });
    expect(parseStream(bytes)).toEqual([
      { _seq: "0", server: "edge-gpu", tool: "generate" },
    ]);
  });

  it("round-trips multiple records in order", () => {
    const bytes = concat([
      dumpRecord(0, { tool: "route" }),
      dumpRecord(1, { tool: "draft" }),
      dumpRecord(2, { tool: "verify" }),
    ]);
    const records = parseStream(bytes);
    expect(records.map((r) => [r._seq, r.tool])).toEqual([
      ["0", "route"],
      ["1", "draft"],
      ["2", "verify"],
    ]);
  });

  it("decodes UTF-8 values (including replacement on bad bytes)", () => {
    const bytes = dumpRecord(0, { greeting: "héllo ✓" });
    expect(parseStream(bytes)).toEqual([
      { _seq: "0", greeting: "héllo ✓" },
    ]);
  });

  it("preserves bytes inside a value that look like grammar tokens", () => {
    // The value contains LF, %%END, %%REC -- all must be read by length, not
    // interpreted as structural tokens.
    const value = "line1\n%%END\nline3\n%%REC v1 99\n";
    const bytes = dumpRecord(0, { payload: value });
    expect(parseStream(bytes)).toEqual([{ _seq: "0", payload: value }]);
  });

  it("skips garbage lines between records", () => {
    const bytes = concat([
      ENCODER.encode("garbage line one\n"),
      ENCODER.encode("garbage line two\n"),
      dumpRecord(0, { tool: "route" }),
      ENCODER.encode("more garbage\n"),
      dumpRecord(1, { tool: "draft" }),
    ]);
    expect(parseStream(bytes)).toEqual([
      { _seq: "0", tool: "route" },
      { _seq: "1", tool: "draft" },
    ]);
  });

  it("applies `keep` projection without affecting structural validation", () => {
    const bytes = dumpRecord(0, {
      server: "edge-gpu",
      tool: "generate",
      args: "very long ignored payload",
    });
    const records = parseStream(bytes, new Set(["tool"]));
    // _seq is always kept; only the requested field comes through.
    expect(records).toEqual([{ _seq: "0", tool: "generate" }]);
  });
});

describe("parseStreamIncremental — truncation rewind", () => {
  it("rewinds nextOffset past a complete prefix when the trailing record is truncated", () => {
    const complete = dumpRecord(0, { tool: "route" });
    const trailing = dumpRecord(1, { tool: "draft" });
    // Drop the last 5 bytes of the trailing record -- it must be retried.
    const bytes = concat([
      complete,
      trailing.subarray(0, trailing.length - 5),
    ]);
    const { records, nextOffset } = parseStreamIncremental(bytes);
    expect(records).toEqual([{ _seq: "0", tool: "route" }]);
    expect(nextOffset).toBe(complete.length);
  });

  it("resumes cleanly once the truncated record is completed", () => {
    const a = dumpRecord(0, { tool: "route" });
    const b = dumpRecord(1, { tool: "draft" });
    const partial = concat([a, b.subarray(0, b.length - 3)]);
    const first = parseStreamIncremental(partial);
    expect(first.records).toEqual([{ _seq: "0", tool: "route" }]);
    expect(first.nextOffset).toBe(a.length);

    const grown = concat([a, b]); // the writer wrote the rest
    const second = parseStreamIncremental(grown, first.nextOffset);
    expect(second.records).toEqual([{ _seq: "1", tool: "draft" }]);
    expect(second.nextOffset).toBe(grown.length);
  });

  it("does not yield a partially-decoded record on truncation", () => {
    const b = dumpRecord(0, { tool: "draft" });
    // Cut inside the value bytes.
    const cut = b.subarray(0, Math.floor(b.length / 2));
    const { records, nextOffset } = parseStreamIncremental(cut);
    expect(records).toEqual([]);
    expect(nextOffset).toBe(0);
  });
});

describe("cross-check against the canonical Python parser", () => {
  it("matches `cascade.logfmt.parse_stream` on a real .rec fixture", () => {
    const bytes = readFileSync(join(FIXTURE_DIR, "sample.rec"));
    const expected = JSON.parse(
      readFileSync(join(FIXTURE_DIR, "sample.parsed.json"), "utf-8"),
    ) as Record<string, string>[];
    const actual = parseStream(new Uint8Array(bytes));
    expect(actual).toEqual(expected);
  });
});
