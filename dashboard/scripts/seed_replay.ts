/**
 * Replay an existing `.rec` lane into a fixture lane at a controlled rate.
 *
 * Two uses:
 *   1. Offline demo: drive the dashboard without the cascade being busy.
 *   2. CI smoke: the end-to-end test seeds a tmp `.rec` and asserts the
 *      record makes it through tailer -> store -> hub -> OOB frame.
 *
 * Usage (from the dashboard package root, after `npm install`):
 *   tsx scripts/seed_replay.ts \
 *       --source ../runs/edge-gpu.rec \
 *       --target /tmp/_seed.rec \
 *       --rate 5 \
 *       [--loop] [--rewrite-ts] [--max <n>]
 *
 * The source is parsed with the canonical `parseStream`; each record is
 * re-emitted into the target with `dumpRecord`, optionally rewriting `ts`
 * to "now" so live-window panels (sparkline, rate meter) actually paint.
 * The seq is renumbered monotonically across the appended stream so consumers
 * stay happy.
 */
import { promises as fs } from "node:fs";
import { resolve } from "node:path";

import { dumpRecord, parseStream } from "../src/lib/logfmt.js";

export interface ReplayOptions {
  readonly sourcePath: string;
  readonly targetPath: string;
  /** Records per second. */
  readonly ratePerSec?: number;
  /** Loop the source forever (default: stop after one pass). */
  readonly loop?: boolean;
  /** Replace each record's `ts` field with the wall-clock at the time it
   * gets appended (default: true -- live panels need fresh timestamps). */
  readonly rewriteTs?: boolean;
  /** Stop after this many appended records (default: unbounded except for
   * the natural end of the source when `loop` is false). */
  readonly max?: number;
  /** Hook fired immediately after each successful append. Test surface --
   * the production driver doesn't pass this. */
  readonly onAppend?: (record: Record<string, string>) => void;
}

export interface ReplayHandle {
  readonly stop: () => void;
  /** Resolves when the replay has appended all scheduled records (or `stop`
   * was called). */
  readonly done: Promise<void>;
}

const DEFAULT_RATE = 5;

export function startReplay(options: ReplayOptions): ReplayHandle {
  const ratePerSec = options.ratePerSec ?? DEFAULT_RATE;
  if (ratePerSec <= 0) {
    throw new Error(`ratePerSec must be > 0 (got ${String(ratePerSec)})`);
  }
  const rewriteTs = options.rewriteTs ?? true;
  const intervalMs = 1000 / ratePerSec;

  let stopped = false;
  let resolveDone: () => void = () => undefined;
  const done = new Promise<void>((resolve_) => {
    resolveDone = resolve_;
  });

  void (async () => {
    try {
      let written = 0;
      let outSeq = 0;
      // Read once -- the source is treated as a finite recording. For long
      // runs the source can be re-read at each loop iteration.
      const sourceBytes = await fs.readFile(options.sourcePath);
      let records = parseStream(new Uint8Array(sourceBytes));
      if (records.length === 0) {
        resolveDone();
        return;
      }
      let i = 0;
      while (!stopped) {
        if (i >= records.length) {
          if (!options.loop) break;
          // Re-read so a live source picks up new records between loops.
          const fresh = await fs.readFile(options.sourcePath);
          records = parseStream(new Uint8Array(fresh));
          if (records.length === 0) break;
          i = 0;
        }
        const next = nextRecord(records, i, rewriteTs);
        const bytes = dumpRecord(outSeq, next);
        await fs.appendFile(options.targetPath, bytes);
        options.onAppend?.(next);
        written += 1;
        outSeq += 1;
        i += 1;
        if (options.max !== undefined && written >= options.max) break;
        await sleep(intervalMs);
      }
    } finally {
      resolveDone();
    }
  })();

  return {
    stop: () => {
      stopped = true;
    },
    done,
  };
}

function nextRecord(
  records: readonly Record<string, string>[],
  i: number,
  rewriteTs: boolean,
): Record<string, string> {
  const r = records[i];
  if (!r) return { _seq: "0" };
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(r)) {
    // `_seq` is reassigned by dumpRecord; skip carrying it through.
    if (k === "_seq") continue;
    out[k] = v;
  }
  if (rewriteTs) out.ts = (Date.now() / 1000).toFixed(3);
  return out;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve_) => setTimeout(resolve_, ms));
}

// CLI entry: only runs when invoked as a script (not when imported).
const isCliEntry = (() => {
  try {
    return import.meta.url === new URL(`file://${resolve(process.argv[1] ?? "")}`).href;
  } catch {
    return false;
  }
})();

if (isCliEntry) {
  const args = parseArgs(process.argv.slice(2));
  if (!args.source || !args.target) {
    console.error(
      "usage: tsx scripts/seed_replay.ts --source <path> --target <path> " +
        "[--rate <rec/s>] [--loop] [--max <n>] [--no-rewrite-ts]",
    );
    process.exit(2);
  }
  const handle = startReplay({
    sourcePath: resolve(args.source),
    targetPath: resolve(args.target),
    ratePerSec: args.rate !== undefined ? Number(args.rate) : undefined,
    loop: args.loop === "true",
    rewriteTs: args["no-rewrite-ts"] !== "true",
    max: args.max !== undefined ? Number(args.max) : undefined,
  });
  process.on("SIGINT", () => handle.stop());
  process.on("SIGTERM", () => handle.stop());
  void handle.done.then(() => process.exit(0));
}

function parseArgs(argv: readonly string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === undefined || !a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      out[key] = "true";
    } else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}
