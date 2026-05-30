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
  degenPanelRegion,
  logFeedRegion,
  meshEffectivenessRegion,
  meshEffectivenessView,
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
    expect(html).toContain('id="vinyl-r-degen-panel"');
    expect(html).toContain('id="vinyl-r-mesh-effectiveness"');
    expect(html).toContain('id="vinyl-r-log-feed"');
    // The static topology lives inline in the initial paint (no live-region
    // wrapper) so search engines / curl see the architecture even pre-WS.
    expect(html).toContain('class="topology"');
    // Chain nodes (the real Celery tasks), not abstract tier blobs.
    expect(html).toContain("gpu_solve");
    expect(html).toContain("draft_gate");
  });
});

describe("logFeedRegion (roomy event log)", () => {
  it("renders an empty state when the store is empty", () => {
    const html = renderToString(logFeedRegion.render(app.ctx));
    expect(html).toContain("log-feed empty");
    expect(html).toContain("no records yet");
  });

  it("renders one row per record, newest first, with tier + tool + state", () => {
    app.ctx.store.ingest("edge-npu", { _seq: "0", tool: "route", ts: "100" });
    app.ctx.store.ingest("edge-gpu", {
      _seq: "0",
      tool: "generate",
      ts: "101",
      ok: "false",
    });
    const html = renderToString(logFeedRegion.render(app.ctx));
    // Two rows.
    expect((html.match(/class="log-row/g) ?? []).length).toBe(2);
    // The failing gpu record carries the fail modifier + FAIL state.
    expect(html).toContain("log-row fail");
    expect(html).toContain("FAIL");
    // Newest (gpu, seq later) renders before the npu row.
    expect(html.indexOf("generate")).toBeLessThan(html.indexOf("route"));
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
    // Mirror what app.ts onConnect actually subscribes (6 regions after
    // SD-4 added mesh-effectiveness).
    app.ctx.hub.subscribe(
      TICK,
      conn,
      nowPlayingRegion,
      rateMeterRegion,
      cascadeHealthRegion,
      cascadeFlowRegion,
      degenPanelRegion,
      meshEffectivenessRegion,
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
    // added cascade-health; SD-2b added degen-panel; SD-4 added mesh-eff).
    expect(elements?.length).toBe(6);
    // The frames carry the region IDs the htmx-ws contract expects.
    const allFrames = (elements ?? []).join("");
    expect(allFrames).toContain('id="vinyl-r-now-playing"');
    expect(allFrames).toContain('id="vinyl-r-rate-meter"');
    expect(allFrames).toContain('id="vinyl-r-cascade-flow"');
    expect(allFrames).toContain('id="vinyl-r-cascade-health"');
    expect(allFrames).toContain('id="vinyl-r-degen-panel"');
    expect(allFrames).toContain('id="vinyl-r-mesh-effectiveness"');
    // And the newly rendered nowPlaying reflects the just-ingested record.
    expect(allFrames).toContain(">generate<");
  });

  it("emits TICK on a cascade-degeneration record even though it is not a particle", async () => {
    const conn = mockConn(app.ctx);
    app.ctx.hub.subscribe(TICK, conn, degenPanelRegion);

    await fs.writeFile(
      join(runsDir, "cascade-degeneration.rec"),
      dumpRecord(0, {
        tool: "observe",
        ts: "100",
        tier: "npu",
        score: "0.17",
        degraded: "true",
        reasons: '["looping: trigram_repeat=0.10 > 0.04"]',
      }),
    );
    await app.tailer.tick();

    expect(conn.pushed.length).toBe(1);
    // The store recorded the observation -- the panel reads from this.
    expect(app.ctx.store.degen("npu")).toHaveLength(1);
    expect(app.ctx.store.degen("npu")[0]?.score).toBe(0.17);
  });

  it("emits TICK on a cascade record (SD-4 outcomes lane, not a particle)", async () => {
    const conn = mockConn(app.ctx);
    app.ctx.hub.subscribe(TICK, conn, meshEffectivenessRegion);

    await fs.writeFile(
      join(runsDir, "cascade.rec"),
      dumpRecord(0, {
        tool: "solve",
        ts: "100",
        final_tier: "gpu",
        trace: "mesh|-|0.00s|gpu gate PASS",
      }),
    );
    await app.tailer.tick();

    expect(conn.pushed.length).toBe(1);
    // The store recorded the outcome -- the panel reads from this.
    const o = app.ctx.store.cascadeOutcomes();
    expect(o.total).toBe(1);
    expect(o.resolvedGpu).toBe(1);
    expect(o.effectivenessPct).toBeCloseTo(100, 5);
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

describe("SD-P3 heartbeat (createDashboardApp scheduler chain)", () => {
  it("schedules a heartbeat after a record-driven emit, and self-loops while in-flight animations remain", async () => {
    // Build a scenario-local app with an injected scheduler so we can
    // observe and drive the heartbeat chain without real time.
    const localRunsDir = await fs.mkdtemp(join(tmpdir(), "dashboard-hb-"));
    let now = 100_000;
    const scheduled: Array<{ cb: () => void; ms: number }> = [];
    const localApp = createDashboardApp({
      runsDir: localRunsDir,
      tailerIntervalMs: 1_000_000,
      nowMs: () => now,
      scheduleTimer: (cb, ms) => {
        scheduled.push({ cb, ms });
        return scheduled.length - 1;
      },
    });
    try {
      const conn = mockConn(localApp.ctx);
      localApp.ctx.hub.subscribe(TICK, conn, cascadeFlowRegion);

      await fs.writeFile(
        join(localRunsDir, "edge-gpu.rec"),
        dumpRecord(0, { tool: "generate", ts: "100", latency_ms: "42" }),
      );
      await localApp.tailer.tick();
      // Record-driven emit landed: one push (the cascadeFlowRegion frame).
      expect(conn.pushed).toHaveLength(1);
      // And a heartbeat was scheduled at the canonical 80 ms cadence.
      expect(scheduled).toHaveLength(1);
      expect(scheduled[0]?.ms).toBeGreaterThan(0);
      expect(scheduled[0]?.ms).toBeLessThan(500);

      // Drive the heartbeat callback while the particle is still mid-arc.
      now += scheduled[0]?.ms ?? 0;
      scheduled[0]?.cb();
      // Heartbeat fired its TICK -> another push.
      expect(conn.pushed).toHaveLength(2);
      // And a follow-on heartbeat was scheduled (still in-flight).
      expect(scheduled).toHaveLength(2);

      // Jump past both ANIM_MS and PULSE_MS -- nothing should be animating
      // anymore. The next heartbeat fires its emit (we already scheduled it
      // above) but then refuses to schedule a successor: the chain stops.
      now = 100_000 + 5_000;
      scheduled[1]?.cb();
      expect(conn.pushed).toHaveLength(3);
      expect(scheduled).toHaveLength(2); // no NEW heartbeat scheduled
    } finally {
      localApp.tailer.stop();
      await fs.rm(localRunsDir, { recursive: true, force: true });
    }
  });

  it("does not double-schedule when a record arrives while a heartbeat is in flight", async () => {
    // Single-flight invariant: maybeScheduleHeartbeat must no-op when a
    // timer is already pending. Otherwise a busy ingest stream would stack
    // parallel chains and the dashboard would re-emit at >> 12 Hz.
    const localRunsDir = await fs.mkdtemp(join(tmpdir(), "dashboard-hb-"));
    const scheduled: Array<{ cb: () => void; ms: number }> = [];
    const localApp = createDashboardApp({
      runsDir: localRunsDir,
      tailerIntervalMs: 1_000_000,
      nowMs: () => 100_000,
      scheduleTimer: (cb, ms) => {
        scheduled.push({ cb, ms });
        return scheduled.length - 1;
      },
    });
    try {
      const conn = mockConn(localApp.ctx);
      localApp.ctx.hub.subscribe(TICK, conn, cascadeFlowRegion);

      await fs.writeFile(
        join(localRunsDir, "edge-gpu.rec"),
        dumpRecord(0, { tool: "generate", ts: "100" }),
      );
      await localApp.tailer.tick();
      expect(scheduled).toHaveLength(1);

      // Second record arrives BEFORE the heartbeat callback fires.
      await fs.writeFile(
        join(localRunsDir, "edge-npu.rec"),
        dumpRecord(0, { tool: "draft", ts: "100" }),
      );
      await localApp.tailer.tick();
      // Two record-driven pushes, but still only ONE pending heartbeat.
      expect(conn.pushed).toHaveLength(2);
      expect(scheduled).toHaveLength(1);

      // Fire the heartbeat callback -- after it runs, the handle must be
      // cleared so the chain resumes scheduling. A regression where
      // `heartbeatHandle` never resets would leave `scheduled.length === 1`
      // here because subsequent `maybeScheduleHeartbeat` calls would
      // short-circuit on the stale handle.
      scheduled[0]?.cb();
      expect(conn.pushed).toHaveLength(3); // heartbeat emitted
      expect(scheduled).toHaveLength(2); // chain resumed (still in-flight)
    } finally {
      localApp.tailer.stop();
      await fs.rm(localRunsDir, { recursive: true, force: true });
    }
  });
});

describe("degenPanelRegion", () => {
  function ingestDegen(
    tier: string,
    score: string,
    degraded: string,
    reasons: string,
    seq: number,
  ): void {
    app.ctx.store.ingest("cascade-degeneration", {
      _seq: String(seq),
      tool: "observe",
      ts: "100",
      tier,
      score,
      degraded,
      reasons,
    });
  }

  it("renders an empty row per draft tier when no observations have arrived", () => {
    const html = renderToString(degenPanelRegion.render(app.ctx));
    for (const t of ["npu", "gpu", "igpu"]) {
      expect(html).toContain(`data-tier="${t}"`);
    }
    expect(html).toContain("no obs yet");
    expect(html).not.toContain("degen-row degraded");
  });

  it("paints a row to `degraded` when the most recent obs is degraded", () => {
    ingestDegen("npu", "0.17", "true", '["looping: trigram_repeat=0.10 > 0.04"]', 0);
    const html = renderToString(degenPanelRegion.render(app.ctx));
    expect(html).toMatch(/degen-row degraded[^"]*"[^>]*data-tier="npu"/);
    // The most recent reason tag is surfaced verbatim so the over-trip
    // warning from FINDINGS-pd1-v1-runtime-verification is legible.
    expect(html).toContain("looping: trigram_repeat=0.10 &gt; 0.04");
  });

  it("paints the row as `ok` when the most recent obs is clean (even after earlier degraded)", () => {
    ingestDegen("npu", "0.42", "true", '["looping: trigram_repeat=0.10 > 0.04"]', 0);
    ingestDegen("npu", "0.00", "false", "[]", 1);
    const html = renderToString(degenPanelRegion.render(app.ctx));
    expect(html).toMatch(/degen-row ok[^"]*"[^>]*data-tier="npu"/);
  });

  it("shows N/M where N is degraded count and M is total observations for the tier", () => {
    ingestDegen("npu", "0.17", "true", "[]", 0);
    ingestDegen("npu", "0.25", "true", "[]", 1);
    ingestDegen("npu", "0.00", "false", "[]", 2);
    const html = renderToString(degenPanelRegion.render(app.ctx));
    expect(html).toContain(">2/3<");
  });

  it("emits one <rect> per observation in the bars SVG (newest right)", () => {
    for (let i = 0; i < 4; i++) {
      ingestDegen("gpu", "0.5", "true", "[]", i);
    }
    const html = renderToString(degenPanelRegion.render(app.ctx));
    // 4 obs → 4 rects in the gpu row (one per data point). Each is a 1-wide bar.
    const rectMatches = html.match(/<rect[^>]*class="degen-bar/g) ?? [];
    // 4 from gpu only (npu and igpu have no obs ⇒ empty rows ⇒ no SVG).
    expect(rectMatches.length).toBe(4);
  });

  it("tints clean bars vs degraded bars distinctly", () => {
    ingestDegen("npu", "0.50", "false", "[]", 0);
    ingestDegen("npu", "0.50", "true", "[]", 1);
    const html = renderToString(degenPanelRegion.render(app.ctx));
    expect(html).toContain("degen-bar ok");
    expect(html).toContain("degen-bar degraded");
  });

  it("falls back to a `degraded`/`clean` placeholder when the obs carried no reasons", () => {
    ingestDegen("gpu", "0.17", "true", "[]", 0);
    const html = renderToString(degenPanelRegion.render(app.ctx));
    // Most-recent reason placeholder when reasons[] is empty but degraded=true.
    expect(html).toContain(">degraded<");
  });
});

describe("meshEffectivenessRegion / view (SD-4)", () => {
  it("renders an empty state when no cascade records have arrived", () => {
    const html = renderToString(
      meshEffectivenessView({
        resolvedNpu: 0,
        resolvedIgpu: 0,
        resolvedGpu: 0,
        capped: 0,
        draftSkipped: 0,
        npuGaveUp: 0,
        total: 0,
        effectivenessPct: 0,
      }),
    );
    expect(html).toContain("mesh-eff empty");
    expect(html).toContain("no runs yet");
  });

  it("renders the headline %, total, and all six chips when populated", () => {
    const html = renderToString(
      meshEffectivenessView({
        resolvedNpu: 1,
        resolvedIgpu: 1,
        resolvedGpu: 5,
        capped: 2,
        draftSkipped: 3,
        npuGaveUp: 2,
        total: 9,
        effectivenessPct: (7 / 9) * 100,
      }),
    );
    expect(html).toContain("9 runs");
    expect(html).toContain('class="mesh-eff-chip resolved-npu"');
    expect(html).toContain('class="mesh-eff-chip resolved-igpu"');
    expect(html).toContain('class="mesh-eff-chip resolved-gpu"');
    expect(html).toContain('class="mesh-eff-chip capped"');
    expect(html).toContain('class="mesh-eff-chip skipped"');
    expect(html).toContain('class="mesh-eff-chip npu-gaveup"');
  });

  it("trips the alarm class only when <50% AND total>=5 (small-sample guard)", () => {
    // 1/3 = 33% but total<5 → no alarm.
    const lowSample = renderToString(
      meshEffectivenessView({
        resolvedNpu: 0,
        resolvedIgpu: 0,
        resolvedGpu: 1,
        capped: 2,
        draftSkipped: 0,
        npuGaveUp: 0,
        total: 3,
        effectivenessPct: 33,
      }),
    );
    expect(lowSample).toContain("mesh-eff ok");
    expect(lowSample).not.toContain("mesh-eff alarm");

    // 2/5 = 40% with total=5 → alarm.
    const alarmed = renderToString(
      meshEffectivenessView({
        resolvedNpu: 0,
        resolvedIgpu: 0,
        resolvedGpu: 2,
        capped: 3,
        draftSkipped: 0,
        npuGaveUp: 0,
        total: 5,
        effectivenessPct: 40,
      }),
    );
    expect(alarmed).toContain("mesh-eff alarm");
  });

  it("wires the live region to the store snapshot", () => {
    app.ctx.store.ingest("cascade", {
      _seq: "0",
      tool: "solve",
      ts: "100",
      final_tier: "gpu",
      trace: "mesh|-|0.00s|gpu gate PASS",
    });
    const html = renderToString(meshEffectivenessRegion.render(app.ctx));
    expect(html).toContain("100.0%");
    expect(html).toContain("1 runs");
  });
});
