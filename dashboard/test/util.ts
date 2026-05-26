/** Shared test helpers. Re-exports the writer from the canonical module so
 * the parser-and-its-test-fixture-builder live in one place. */
export { dumpRecord } from "../src/lib/logfmt.js";

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
