import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { dumpRecord, parseStream } from "../src/lib/logfmt.js";
import { startReplay } from "../scripts/seed_replay.js";
import { concat } from "./util.js";

let tmp: string;
let sourcePath: string;
let targetPath: string;

beforeEach(async () => {
  tmp = await fs.mkdtemp(join(tmpdir(), "dashboard-replay-"));
  sourcePath = join(tmp, "source.rec");
  targetPath = join(tmp, "target.rec");
});

afterEach(async () => {
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("startReplay", () => {
  it("appends every source record once at a high rate (no-loop)", async () => {
    await fs.writeFile(
      sourcePath,
      concat([
        dumpRecord(0, { tool: "route" }),
        dumpRecord(1, { tool: "draft" }),
        dumpRecord(2, { tool: "verify" }),
      ]),
    );
    const appended: Record<string, string>[] = [];
    const handle = startReplay({
      sourcePath,
      targetPath,
      ratePerSec: 1000,
      onAppend: (r) => appended.push(r),
    });
    await handle.done;
    expect(appended.map((r) => r.tool)).toEqual(["route", "draft", "verify"]);

    const written = await fs.readFile(targetPath);
    const records = parseStream(new Uint8Array(written));
    expect(records.map((r) => r._seq)).toEqual(["0", "1", "2"]);
    expect(records.every((r) => /^\d+\.\d+$/.test(r.ts ?? ""))).toBe(true);
  });

  it("renumbers _seq monotonically across the appended stream", async () => {
    await fs.writeFile(
      sourcePath,
      concat([
        dumpRecord(42, { tool: "route" }),
        dumpRecord(7, { tool: "draft" }),
      ]),
    );
    const handle = startReplay({
      sourcePath,
      targetPath,
      ratePerSec: 1000,
    });
    await handle.done;
    const records = parseStream(new Uint8Array(await fs.readFile(targetPath)));
    expect(records.map((r) => r._seq)).toEqual(["0", "1"]);
  });

  it("respects --max even with --loop", async () => {
    await fs.writeFile(sourcePath, dumpRecord(0, { tool: "route" }));
    const handle = startReplay({
      sourcePath,
      targetPath,
      ratePerSec: 1000,
      loop: true,
      max: 5,
    });
    await handle.done;
    const records = parseStream(new Uint8Array(await fs.readFile(targetPath)));
    expect(records.length).toBe(5);
  });

  it("can be stopped via the returned handle", async () => {
    await fs.writeFile(sourcePath, dumpRecord(0, { tool: "route" }));
    const handle = startReplay({
      sourcePath,
      targetPath,
      ratePerSec: 1, // very slow
      loop: true,
    });
    // Give it just enough time to append exactly one record.
    await new Promise((r) => setTimeout(r, 50));
    handle.stop();
    await handle.done;
    const records = parseStream(new Uint8Array(await fs.readFile(targetPath)));
    expect(records.length).toBe(1);
  });

  it("preserves --no-rewrite-ts (ts field carried through unchanged)", async () => {
    await fs.writeFile(
      sourcePath,
      dumpRecord(0, { tool: "route", ts: "1700000000.000" }),
    );
    const handle = startReplay({
      sourcePath,
      targetPath,
      ratePerSec: 1000,
      rewriteTs: false,
    });
    await handle.done;
    const records = parseStream(new Uint8Array(await fs.readFile(targetPath)));
    expect(records[0]?.ts).toBe("1700000000.000");
  });

  it("returns immediately on an empty source", async () => {
    await fs.writeFile(sourcePath, new Uint8Array(0));
    const handle = startReplay({ sourcePath, targetPath, ratePerSec: 1000 });
    await handle.done;
    // Target may not even exist -- the script never appended anything.
    await expect(fs.readFile(targetPath)).rejects.toThrow();
  });

  it("rejects a non-positive rate", () => {
    expect(() =>
      startReplay({ sourcePath, targetPath, ratePerSec: 0 }),
    ).toThrow(/ratePerSec/);
  });
});
