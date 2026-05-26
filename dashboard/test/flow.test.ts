import { describe, expect, it } from "vitest";
import { renderToString } from "@danthemanvsqz/vinyl";

import type { DashContext } from "../src/app.js";
import { cascadeFlowRegion, cascadeFlowTopology } from "../src/flow.js";
import { createStore } from "../src/store.js";

function makeCtx(nowMs = 100_000): DashContext {
  return {
    store: createStore(),
    // The hub isn't used by the topology/overlay -- regions read the store
    // synchronously. A typed undefined cast keeps the test cheap.
    hub: undefined as unknown as DashContext["hub"],
    nowMs: () => nowMs,
  };
}

describe("cascadeFlowTopology (static)", () => {
  it("renders an 800x400 SVG with the five zone labels and the five path arcs", () => {
    const html = renderToString(cascadeFlowTopology());
    expect(html).toContain('viewBox="0 0 800 400"');
    // Every zone label appears
    for (const label of [
      "Tier 1 · NPU",
      "Tier 2 · GPU",
      "verify",
      "Tier 3 · Claude CLI",
      "Tier 4 · cloud",
    ]) {
      expect(html).toContain(label);
    }
    // Every path id appears (animateMotion targets in Phase B)
    for (const pathId of [
      "route-to-npu",
      "npu-to-gpu",
      "gpu-to-verify",
      "verify-loop",
      "verify-cap-to-tier3",
      "tier3-to-cloud",
    ]) {
      expect(html).toContain(`id="${pathId}"`);
    }
    // Zone rect styling hooks
    expect(html).toContain('class="zone-rect"');
    expect(html).toContain("zone--npu");
    expect(html).toContain("zone--tier3");
  });
});

describe("cascadeFlowRegion (overlay live region)", () => {
  it("renders an empty overlay with sparkline polylines for each tier (zero pts)", () => {
    const ctx = makeCtx();
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain('class="overlay"');
    for (const tier of ["npu", "gpu", "verify", "cloud"] as const) {
      expect(html).toContain(`sparkline--${tier}`);
    }
    // tier-stat text -- count is "0" for each
    expect(html.match(/class="tier-stat /g)?.length ?? 0).toBe(4);
    // No particles yet
    expect(html.match(/class="particle particle--/g)).toBeNull();
  });

  it("emits one particle circle per ingested record with the correct tier class", () => {
    const ctx = makeCtx();
    ctx.store.ingest("edge-npu", { _seq: "0", tool: "route", ts: "100" });
    ctx.store.ingest("edge-gpu", { _seq: "0", tool: "generate", ts: "100" });
    ctx.store.ingest("edge-verify", {
      _seq: "0",
      tool: "verify_functional",
      ts: "100",
      ok: "false",
    });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain('particle particle--npu"');
    expect(html).toContain('particle particle--gpu"');
    // verify failure adds the fail modifier
    expect(html).toContain("particle particle--verify particle--fail");
  });

  it("caps visible particles per zone at PARTICLES_PER_ZONE (12)", () => {
    const ctx = makeCtx();
    for (let i = 0; i < 30; i++) {
      ctx.store.ingest("edge-npu", { _seq: String(i), tool: "route", ts: "100" });
    }
    const html = renderToString(cascadeFlowRegion.render(ctx));
    const matches = html.match(/particle--npu/g) ?? [];
    expect(matches.length).toBe(12);
  });

  it("tier-stat reflects the count of particles in each tier's pool", () => {
    const ctx = makeCtx();
    ctx.store.ingest("edge-npu", { _seq: "0", tool: "route", ts: "100" });
    ctx.store.ingest("edge-npu", { _seq: "1", tool: "route", ts: "100" });
    ctx.store.ingest("edge-gpu", { _seq: "0", tool: "generate", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    // The stat texts are `<text class="tier-stat tier-stat--<tier>">N</text>`.
    expect(html).toMatch(/tier-stat tier-stat--npu[^>]*>2</);
    expect(html).toMatch(/tier-stat tier-stat--gpu[^>]*>1</);
    expect(html).toMatch(/tier-stat tier-stat--verify[^>]*>0</);
    expect(html).toMatch(/tier-stat tier-stat--cloud[^>]*>0</);
  });

  it("sparkline polyline has 60 points (one per 1-second bucket)", () => {
    const ctx = makeCtx();
    ctx.store.ingest("edge-npu", { _seq: "0", tool: "route", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    // Extract the npu sparkline's `points=` and count comma-pairs.
    const m = html.match(/sparkline--npu"\s+points="([^"]+)"/);
    expect(m).not.toBeNull();
    const pts = m?.[1]?.trim().split(/\s+/) ?? [];
    expect(pts.length).toBe(60);
  });
});
