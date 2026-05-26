/** Shared test helpers. Not exported from the dashboard package. */
const ENCODER = new TextEncoder();

/** Mirror of `cascade.logfmt.dump_record`. Throws on keys the parser would
 * reject so a test cannot fabricate a sequence the canonical parser would
 * never produce. */
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

export function concat(chunks: Uint8Array[]): Uint8Array {
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
