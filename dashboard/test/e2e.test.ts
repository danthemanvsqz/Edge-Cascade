import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { createServer } from "node:http";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { WebSocket } from "ws";

import { createDashboardApp } from "../src/app.js";
import type { DashboardApp } from "../src/app.js";
import { dumpRecord } from "../src/lib/logfmt.js";
import { startReplay } from "../scripts/seed_replay.js";

let tmp: string;
let runsDir: string;
let sourcePath: string;
let app: DashboardApp;
let server: Server;

beforeEach(async () => {
  tmp = await fs.mkdtemp(join(tmpdir(), "dashboard-e2e-"));
  runsDir = join(tmp, "runs");
  await fs.mkdir(runsDir);
  sourcePath = join(tmp, "source.rec");
  // Seed source: one well-formed record. The replayer renumbers + rewrites
  // `ts` so the live-window panels paint.
  await fs.writeFile(
    sourcePath,
    dumpRecord(0, {
      server: "edge-gpu",
      tool: "generate",
      args: '{"prompt":"hi"}',
      ok: "true",
      latency_ms: "123.4",
    }),
  );

  app = createDashboardApp({ runsDir, tailerIntervalMs: 25 });
  app.tailer.start();
  server = createServer((_req, res) => {
    res.writeHead(404);
    res.end();
  });
  server.on("upgrade", (req, socket, head) => {
    app.vws.handleUpgrade(req, socket, head);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
});

afterEach(async () => {
  app.tailer.stop();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("end-to-end smoke: .rec -> tailer -> store -> hub -> WS", () => {
  it("delivers an OOB frame containing the three region IDs after a replayed record", async () => {
    const port = (server.address() as AddressInfo).port;
    const ws = new WebSocket(`ws://127.0.0.1:${String(port)}/ws`);
    await new Promise<void>((resolve, reject) => {
      ws.once("open", () => resolve());
      ws.once("error", reject);
    });

    const messageReceived = new Promise<string>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("no WS message within 3s")),
        3000,
      );
      ws.once("message", (data) => {
        clearTimeout(timer);
        resolve(Buffer.isBuffer(data) ? data.toString("utf8") : String(data));
      });
    });

    const handle = startReplay({
      sourcePath,
      targetPath: join(runsDir, "edge-gpu.rec"),
      ratePerSec: 1000,
      max: 1,
    });

    const text = await messageReceived;
    await handle.done;

    // The WS frame is the concatenation of three OOB strings (one per region)
    // -- htmx applies all top-level elements in a single settle pass.
    expect(text).toContain('id="vinyl-r-now-playing"');
    expect(text).toContain('id="vinyl-r-rate-meter"');
    expect(text).toContain('id="vinyl-r-cascade-flow"');
    // And the replayed record drove the panel content (the GPU tool).
    expect(text).toContain(">generate<");

    ws.close();
    await new Promise<void>((resolve) => ws.once("close", () => resolve()));
  });
});
