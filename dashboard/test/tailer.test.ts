import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { TailedRecord } from "../src/lib/tailer.js";
import { createTailer } from "../src/lib/tailer.js";
import { concat, dumpRecord } from "./util.js";

const ENCODER = new TextEncoder();

let runsDir: string;
let received: TailedRecord[];

beforeEach(async () => {
  runsDir = await fs.mkdtemp(join(tmpdir(), "dashboard-tailer-"));
  received = [];
});

afterEach(async () => {
  await fs.rm(runsDir, { recursive: true, force: true });
});

function makeTailer(keep?: ReadonlySet<string>) {
  return createTailer({
    runsDir,
    onRecord: (r) => received.push(r),
    intervalMs: 1_000_000, // effectively disabled; tests drive `tick` manually
    keep,
  });
}

async function append(name: string, bytes: Uint8Array): Promise<void> {
  await fs.appendFile(join(runsDir, name), bytes);
}

describe("tailer", () => {
  it("emits nothing when the runs dir is empty", async () => {
    const tailer = makeTailer();
    await tailer.tick();
    expect(received).toEqual([]);
  });

  it("emits records from a single .rec file, derives server from filename", async () => {
    await append("edge-npu.rec", dumpRecord(0, { tool: "route" }));
    const tailer = makeTailer();
    await tailer.tick();
    expect(received).toEqual([
      { server: "edge-npu", record: { _seq: "0", tool: "route" } },
    ]);
  });

  it("ignores non-matching filenames", async () => {
    await append("notes.txt", ENCODER.encode("not a rec file"));
    await append("edge-gpu.rec", dumpRecord(0, { tool: "generate" }));
    const tailer = makeTailer();
    await tailer.tick();
    expect(received.map((r) => r.server)).toEqual(["edge-gpu"]);
  });

  it("emits records incrementally across ticks", async () => {
    const tailer = makeTailer();
    await append("edge-npu.rec", dumpRecord(0, { tool: "route" }));
    await tailer.tick();
    expect(received.length).toBe(1);

    await append("edge-npu.rec", dumpRecord(1, { tool: "route" }));
    await tailer.tick();
    expect(received.length).toBe(2);
    expect(received.map((r) => r.record._seq)).toEqual(["0", "1"]);
  });

  it("does not emit a truncated trailing record until it completes", async () => {
    const full = dumpRecord(0, { tool: "draft" });
    const tailer = makeTailer();
    // Write only half the bytes.
    await append("edge-gpu.rec", full.subarray(0, Math.floor(full.length / 2)));
    await tailer.tick();
    expect(received).toEqual([]);

    // Now append the rest -- the SAME record should appear exactly once.
    await append("edge-gpu.rec", full.subarray(Math.floor(full.length / 2)));
    await tailer.tick();
    expect(received).toEqual([
      { server: "edge-gpu", record: { _seq: "0", tool: "draft" } },
    ]);
  });

  it("skips top-level garbage lines and resumes", async () => {
    await append("edge-npu.rec", ENCODER.encode("garbage line\n"));
    await append("edge-npu.rec", dumpRecord(0, { tool: "route" }));
    const tailer = makeTailer();
    await tailer.tick();
    expect(received.map((r) => r.record._seq)).toEqual(["0"]);
  });

  it("re-reads from zero when the inode changes (logrotate unlink+recreate, even to a LARGER file)", async () => {
    await append("edge-gpu.rec", dumpRecord(0, { tool: "route" }));
    const tailer = makeTailer();
    await tailer.tick();
    expect(received.length).toBe(1);

    // logrotate-style: unlink the original (releases the inode), then create
    // a NEW file at the same path with strictly more content. The size-only
    // check would silently skip the first record of the replacement; the
    // inode check catches it.
    await fs.unlink(join(runsDir, "edge-gpu.rec"));
    await append(
      "edge-gpu.rec",
      concat([
        dumpRecord(0, { tool: "draft" }),
        dumpRecord(1, { tool: "verify" }),
      ]),
    );
    await tailer.tick();
    const tools = received.map((r) => r.record.tool);
    expect(tools).toEqual(["route", "draft", "verify"]);
  });

  it("re-reads the file from zero when it shrinks (truncation/rotation)", async () => {
    await append("edge-gpu.rec", dumpRecord(0, { tool: "route" }));
    const tailer = makeTailer();
    await tailer.tick();
    expect(received.length).toBe(1);

    // Truncate-and-rewrite with a shorter replacement: size < cached.consumed
    // triggers rotation detection. Real-world analog: logrotate truncated the
    // file in place (the cascade itself is append-only, but the dashboard is
    // robust to either).
    await fs.writeFile(join(runsDir, "edge-gpu.rec"), dumpRecord(0, { x: "y" }));
    await tailer.tick();
    expect(received.length).toBe(2);
    expect(received[1]?.record).toEqual({ _seq: "0", x: "y" });
  });

  it("tails multiple files concurrently", async () => {
    await append("edge-npu.rec", dumpRecord(0, { tool: "route" }));
    await append("edge-gpu.rec", dumpRecord(0, { tool: "generate" }));
    const tailer = makeTailer();
    await tailer.tick();
    const seen = new Set(received.map((r) => `${r.server}:${r.record.tool ?? ""}`));
    expect(seen).toEqual(
      new Set(["edge-npu:route", "edge-gpu:generate"]),
    );
  });

  it("applies the `keep` projection (passes through to the parser)", async () => {
    await append(
      "edge-gpu.rec",
      dumpRecord(0, {
        tool: "generate",
        args: "ignored payload",
        result: "ignored too",
      }),
    );
    const tailer = makeTailer(new Set(["tool"]));
    await tailer.tick();
    expect(received).toEqual([
      { server: "edge-gpu", record: { _seq: "0", tool: "generate" } },
    ]);
  });

  it("survives a tick against a missing runs dir without throwing", async () => {
    await fs.rm(runsDir, { recursive: true, force: true });
    const tailer = makeTailer();
    await expect(tailer.tick()).resolves.toBeUndefined();
    expect(received).toEqual([]);
    // recreate so afterEach can rm cleanly
    await fs.mkdir(runsDir, { recursive: true });
  });

  it("handles byte-by-byte appends without losses or duplicates", async () => {
    const tailer = makeTailer();
    const bytes = concat([
      dumpRecord(0, { tool: "route" }),
      dumpRecord(1, { tool: "draft" }),
      dumpRecord(2, { tool: "verify" }),
    ]);
    for (let i = 0; i < bytes.length; i++) {
      await append("edge-npu.rec", bytes.subarray(i, i + 1));
      await tailer.tick();
    }
    expect(received.map((r) => r.record._seq)).toEqual(["0", "1", "2"]);
  });
});
