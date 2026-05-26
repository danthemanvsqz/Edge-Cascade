import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import type { IncomingMessage } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { renderToString } from "@danthemanvsqz/vinyl";
import type { VinylConnection } from "@danthemanvsqz/vinyl";

import type { DashContext, DashboardApp } from "../src/app.js";
import { createDashboardApp } from "../src/app.js";
import { cascadeFlowRegion } from "../src/flow.js";
import {
  cascadeHealthRegion,
  nowPlayingRegion,
  rateMeterRegion,
  TICK,
} from "../src/panels.js";
import { dumpRecord } from "./util.js";

let runsDir: string;
let app: DashboardApp;

beforeEach(async () => {
  runsDir = await fs.mkdtemp(join(tmpdir(), "dashboard-app-"));
  app = createDashboardApp({
    runsDir,
    tailerIntervalMs: 1_000_000, // disable auto-poll; tests drive tick()
    nowMs: () => 100_000, // pinned for sparkline/rate predictability
  });
});

afterEach(async () => {
  app.tailer.stop();
  await fs.rm(runsDir, { recursive: true, force: true });
});

/** Minimal mock connection -- the hub only uses `.context` and `.push`. The
 * socket / req are never inspected so a typed cast is safe. */
function mockConn(
  ctx: DashContext,
): VinylConnection<DashContext> & { pushed: string[][] } {
  const pushed: string[][] = [];
  return {
    socket: {} as VinylConnection<DashContext>["socket"],
    req: {} as IncomingMessage,
    context: ctx,
    send() {
      /* unused */
    },
    push(...elements: string[]) {
      pushed.push(elements);
    },
    close() {
      /* unused */
    },
    pushed,
  };
}

describe("page render", () => {
  it("includes the htmx + htmx-ws bootstrap and the region mounts", () => {
    const html = renderToString(app.page());
    // htmx core + ws extension
    expect(html).toContain('src="https://unpkg.com/htmx.org@2.0.4"');
    expect(html).toContain('src="https://unpkg.com/htmx-ext-ws@2.0.2"');
    // Connect on /ws and mark the body as htmx-ws-aware
    expect(html).toContain('hx-ext="ws"');
    expect(html).toContain('ws-connect="/ws"');
    // The three live-region mounts (regionId derives from `vinyl-r-<key>`).
    expect(html).toContain('id="vinyl-r-now-playing"');
    expect(html).toContain('id="vinyl-r-rate-meter"');
    expect(html).toContain('id="vinyl-r-cascade-flow"');
    expect(html).toContain('id="vinyl-r-cascade-health"');
    // The static topology lives inline in the initial paint (no live-region
    // wrapper) so search engines / curl see the architecture even pre-WS.
    expect(html).toContain('class="topology"');
    expect(html).toContain("Tier 1 · NPU");
    expect(html).toContain("Tier 4 · cloud");
  });
});

describe("nowPlayingRegion", () => {
  it("renders an empty state when the store is empty", () => {
    const html = renderToString(nowPlayingRegion.render(app.ctx));
    expect(html).toContain("waiting for activity");
  });

  it("renders the most recent record's tier + tool + latency", () => {
    app.ctx.store.ingest("edge-gpu", {
      _seq: "3",
      tool: "generate",
      latency_ms: "1250",
      ok: "true",
    });
    const html = renderToString(nowPlayingRegion.render(app.ctx));
    expect(html).toContain('data-tier="gpu"');
    expect(html).toContain(">generate<");
    expect(html).toContain("1.25 s");
    expect(html).toContain("now-playing ok");
  });

  it("flips to fail class on ok=false", () => {
    app.ctx.store.ingest("edge-verify", {
      _seq: "0",
      tool: "verify_functional",
      ok: "false",
    });
    const html = renderToString(nowPlayingRegion.render(app.ctx));
    expect(html).toContain("now-playing fail");
  });
});

describe("rateMeterRegion", () => {
  it("renders zero state initially", () => {
    const html = renderToString(rateMeterRegion.render(app.ctx));
    expect(html).toContain("0 records");
    expect(html).toContain("0.0 rec/s");
    expect(html).toContain("spend clean");
    expect(html).toContain("$0.00");
  });

  it("counts records and reflects clean spend after ingests", () => {
    for (let i = 0; i < 3; i++) {
      app.ctx.store.ingest("edge-npu", {
        _seq: String(i),
        tool: "route",
        ts: "100",
      });
    }
    const html = renderToString(rateMeterRegion.render(app.ctx));
    expect(html).toContain("3 records");
    expect(html).toContain("spend clean");
  });

  it("turns the spend badge dirty when a cloud generate lands", () => {
    app.ctx.store.ingest("edge-cloud", {
      _seq: "0",
      tool: "generate",
      result: JSON.stringify({ est_cost_usd: 0.07 }),
    });
    const html = renderToString(rateMeterRegion.render(app.ctx));
    expect(html).toContain("spend dirty");
    expect(html).toContain("$0.07");
    expect(html).toContain("1 cloud");
  });
});

describe("cascadeHealthRegion", () => {
  it("renders a baseline-clean row with all four tiers `unseen`", () => {
    const html = renderToString(cascadeHealthRegion.render(app.ctx));
    expect(html).toContain('class="cascade-health ok"');
    for (const t of ["npu", "gpu", "verify", "cloud"]) {
      expect(html).toContain(`data-tier="${t}"`);
      expect(html).toContain(`tier-health unseen`);
    }
    expect(html).not.toContain("cascade-health degraded");
  });

  it("flips the row to degraded + the tier to `down` after a status with available:false", () => {
    app.ctx.store.ingest("edge-npu", {
      _seq: "0",
      tool: "status",
      ts: "100",
      result: JSON.stringify({ available: false, reason: "model missing" }),
    });
    const html = renderToString(cascadeHealthRegion.render(app.ctx));
    expect(html).toContain('class="cascade-health degraded"');
    // The NPU badge specifically -- not a generic "any tier" classname.
    expect(html).toMatch(/tier-health down[^"]*"[^>]*data-tier="npu"|data-tier="npu"[^>]*tier-health down/);
  });
});

describe("tailer -> hub wiring", () => {
  it("emits TICK and pushes OOB frames to subscribers after an ingested record", async () => {
    const conn = mockConn(app.ctx);
    // Mirror what app.ts onConnect actually subscribes (4 regions after
    // SD-2 added cascade-health).
    app.ctx.hub.subscribe(
      TICK,
      conn,
      nowPlayingRegion,
      rateMeterRegion,
      cascadeHealthRegion,
      cascadeFlowRegion,
    );

    await fs.writeFile(
      join(runsDir, "edge-gpu.rec"),
      dumpRecord(7, { tool: "generate", ts: "100", latency_ms: "42" }),
    );
    await app.tailer.tick();

    expect(conn.pushed.length).toBe(1);
    const elements = conn.pushed[0];
    expect(elements).toBeDefined();
    // Each subscribed region contributes one OOB string per emit (slice 5
    // added now-playing + rate-meter; slice 6 added cascade-flow; SD-2
    // added cascade-health).
    expect(elements?.length).toBe(4);
    // The frames carry the region IDs the htmx-ws contract expects.
    const allFrames = (elements ?? []).join("");
    expect(allFrames).toContain('id="vinyl-r-now-playing"');
    expect(allFrames).toContain('id="vinyl-r-rate-meter"');
    expect(allFrames).toContain('id="vinyl-r-cascade-flow"');
    expect(allFrames).toContain('id="vinyl-r-cascade-health"');
    // And the newly rendered nowPlaying reflects the just-ingested record.
    expect(allFrames).toContain(">generate<");
  });

  it("does NOT emit when an unknown-server record arrives (experiment lane)", async () => {
    const conn = mockConn(app.ctx);
    app.ctx.hub.subscribe(TICK, conn, nowPlayingRegion, rateMeterRegion);

    await fs.writeFile(
      join(runsDir, "experiment-cp5.rec"),
      dumpRecord(0, { tool: "verify_functional" }),
    );
    await app.tailer.tick();

    expect(conn.pushed).toEqual([]);
  });

  it("startFromEof plumbs through to the tailer (SD-3 session-coupling)", async () => {
    // Pre-existing content -- this would render under the default factory.
    await fs.writeFile(
      join(runsDir, "edge-npu.rec"),
      dumpRecord(0, { tool: "route", ts: "100" }),
    );
    // Re-create the app with startFromEof; its tailer should snapshot the
    // existing record away on the first tick. (`app` from beforeEach is
    // stopped + replaced for this case so the tmp dir cleanup still works.)
    app.tailer.stop();
    app = createDashboardApp({
      runsDir,
      tailerIntervalMs: 1_000_000,
      nowMs: () => 100_000,
      startFromEof: true,
    });
    const conn = mockConn(app.ctx);
    app.ctx.hub.subscribe(
      TICK,
      conn,
      nowPlayingRegion,
      rateMeterRegion,
      cascadeHealthRegion,
      cascadeFlowRegion,
    );
    await app.tailer.tick();
    expect(conn.pushed).toEqual([]); // pre-existing record skipped

    // A record appended AFTER first-tick must still flow through.
    await fs.appendFile(
      join(runsDir, "edge-npu.rec"),
      dumpRecord(1, { tool: "draft", ts: "100" }),
    );
    await app.tailer.tick();
    expect(conn.pushed.length).toBe(1);
  });

  it("drops a connection's subscriptions on onClose", () => {
    const conn = mockConn(app.ctx);
    app.ctx.hub.subscribe(TICK, conn, nowPlayingRegion);
    expect(app.ctx.hub.size).toBe(1);
    app.ctx.hub.remove(conn);
    expect(app.ctx.hub.size).toBe(0);
  });
});
