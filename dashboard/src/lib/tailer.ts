/**
 * Multi-file tailer over `runs/*.rec`. On each tick: enumerate the runs dir,
 * stat each match, read any new bytes from a per-file cached offset, feed them
 * through `parseStreamIncremental`, and fire `onRecord` for each completed
 * record. The cached offset + a small remainder buffer let an in-flight
 * (truncated) trailing record be retried on the next tick without re-reading
 * the file from zero. Rotation/truncation (file size < cached size) resets
 * the per-file state.
 *
 * Polling is the *primary* implementation rather than `fs.watch`, by design:
 * `fs.watch` semantics differ across platforms (Windows in particular drops
 * events and lacks rename detection), so the simpler poll loop is the source
 * of truth. A future `fs.watch` integration would only be a wake-up hint that
 * shortens the wait to the next tick.
 *
 * The tailer is intentionally unaware of Vinyl. The consumer (server.ts)
 * passes an `onRecord` callback that updates the store and emits to the
 * SignalHub -- so this module is testable headless, and the dashboard's
 * push fabric can be swapped without touching the tailer.
 */
import { promises as fs } from "node:fs";
import { join } from "node:path";

import { parseStreamIncremental } from "./logfmt.js";

export interface TailedRecord {
  /** The server name derived from the file name (`edge-npu.rec` -> `edge-npu`). */
  readonly server: string;
  /** The record itself: `_seq` plus any fields the `keep` projection let through. */
  readonly record: Record<string, string>;
}

export interface TailerOptions {
  readonly runsDir: string;
  readonly onRecord: (r: TailedRecord) => void;
  /** File-name pattern; first capture group is the server name. */
  readonly pattern?: RegExp;
  /** Poll cadence. */
  readonly intervalMs?: number;
  /** Field projection (passed through to `parseStreamIncremental`). */
  readonly keep?: ReadonlySet<string>;
}

export interface Tailer {
  /** Begin polling. Idempotent — calling twice does nothing on the second call. */
  start(): void;
  /** Stop polling. Idempotent. */
  stop(): void;
  /** Run one poll pass synchronously (resolves when complete). Test surface;
   * production code should rely on `start()` + the poll loop. */
  tick(): Promise<void>;
}

interface FileState {
  /** Byte offset into the file at which we have fully parsed. */
  offset: number;
  /** Bytes we have read past `offset` but could not yet finish parsing
   * (truncated trailing record). Held for the next tick. */
  pending: Uint8Array;
}

const DEFAULT_PATTERN = /^(.+)\.rec$/;
const DEFAULT_INTERVAL_MS = 250;

export function createTailer(options: TailerOptions): Tailer {
  const pattern = options.pattern ?? DEFAULT_PATTERN;
  const intervalMs = options.intervalMs ?? DEFAULT_INTERVAL_MS;
  const state = new Map<string, FileState>();
  let timer: NodeJS.Timeout | null = null;
  let running = false;

  async function tick(): Promise<void> {
    let entries: string[];
    try {
      entries = await fs.readdir(options.runsDir);
    } catch {
      // Dir doesn't exist yet (cascade not started). Wait for the next tick.
      return;
    }
    for (const entry of entries) {
      const match = pattern.exec(entry);
      if (!match || match[1] === undefined) continue;
      const server = match[1];
      const path = join(options.runsDir, entry);
      await ingestFile(server, path);
    }
  }

  async function ingestFile(server: string, path: string): Promise<void> {
    let size: number;
    try {
      const stat = await fs.stat(path);
      if (!stat.isFile()) return;
      size = stat.size;
    } catch {
      return; // file vanished between readdir and stat
    }
    let cur = state.get(path);
    if (!cur) {
      cur = { offset: 0, pending: new Uint8Array(0) };
      state.set(path, cur);
    }
    const consumed = cur.offset + cur.pending.length;
    if (size < consumed) {
      // Rotation / truncation. Reset and re-read from zero.
      cur.offset = 0;
      cur.pending = new Uint8Array(0);
    }
    const newBytes = size - (cur.offset + cur.pending.length);
    if (newBytes <= 0) return;

    const buf = Buffer.alloc(newBytes);
    const handle = await fs.open(path, "r");
    try {
      await handle.read(buf, 0, newBytes, cur.offset + cur.pending.length);
    } finally {
      await handle.close();
    }
    const fresh = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    const combined = concatBytes(cur.pending, fresh);

    const { records, nextOffset } = parseStreamIncremental(
      combined,
      0,
      options.keep,
    );
    for (const record of records) {
      options.onRecord({ server, record });
    }
    cur.offset += nextOffset;
    cur.pending = combined.subarray(nextOffset);
  }

  return {
    start() {
      if (timer) return;
      timer = setInterval(() => {
        if (running) return; // skip overlapping ticks
        running = true;
        tick().finally(() => {
          running = false;
        });
      }, intervalMs);
      timer.unref();
    },
    stop() {
      if (!timer) return;
      clearInterval(timer);
      timer = null;
    },
    tick,
  };
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  if (a.length === 0) return b;
  if (b.length === 0) return a;
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}
