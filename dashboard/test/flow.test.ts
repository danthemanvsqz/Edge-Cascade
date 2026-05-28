import { describe, expect, it } from "vitest";
import { renderToString } from "@danthemanvsqz/vinyl";

import type { DashContext } from "../src/app.js";
import {
  ANIM_MS,
  cascadeFlowRegion,
  cascadeFlowTopology,
  enteringArcStart,
  isTierPulsing,
  particlePosition,
  PULSE_MS,
} from "../src/flow.js";
import { createStore } from "../src/store.js";
import type { Particle, Tier } from "../src/store.js";

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
    // SD-P1 appends an optional `particle--in-flight` suffix when ageMs <
    // ANIM_MS, so the previous exact-end class match no longer holds; assert
    // the tier-class prefix instead (the only thing this test cares about).
    expect(html).toContain("particle particle--npu");
    expect(html).toContain("particle particle--gpu");
    // verify failure adds the fail modifier (between tier and in-flight)
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

// SD-P1: particles ride entering arcs ------------------------------------

function part(tier: Tier, tsMs: number, seq = 0): Particle {
  return {
    id: `p-edge-${tier}-${seq}`,
    tier,
    server: `edge-${tier}`,
    seq: String(seq),
    tool: "anything",
    tsMs,
    latencyMs: 0,
    ok: true,
  };
}

describe("enteringArcStart (SD-P1)", () => {
  it("returns an off-zone start for each of the four record-stream tiers", () => {
    for (const tier of ["npu", "gpu", "verify", "cloud"] as const) {
      const start = enteringArcStart(tier);
      expect(start).not.toBeNull();
    }
  });

  it("npu starts at x=0 (route origin, off the left edge of all zones)", () => {
    expect(enteringArcStart("npu")?.x).toBe(0);
  });
});

describe("particlePosition (SD-P1)", () => {
  it("places a freshly-ingested particle at the arc start (progress=0)", () => {
    const p = part("gpu", 100_000);
    const pos = particlePosition(p, 0, 100_000);
    const start = enteringArcStart("gpu");
    expect(start).not.toBeNull();
    expect(pos.cx).toBeCloseTo(start!.x, 5);
    expect(pos.cy).toBeCloseTo(start!.y, 5);
    expect(pos.inFlight).toBe(true);
  });

  it("interpolates linearly from arc start to pool slot at progress=0.5", () => {
    const p = part("gpu", 100_000);
    const midPos = particlePosition(p, 0, 100_000 + ANIM_MS / 2);
    const startPos = particlePosition(p, 0, 100_000);
    const endPos = particlePosition(p, 0, 100_000 + ANIM_MS);
    expect(midPos.cx).toBeCloseTo((startPos.cx + endPos.cx) / 2, 4);
    expect(midPos.cy).toBeCloseTo((startPos.cy + endPos.cy) / 2, 4);
    expect(midPos.inFlight).toBe(true);
  });

  it("pools at the slot position once age >= ANIM_MS (inFlight false)", () => {
    const p = part("gpu", 100_000);
    const pos = particlePosition(p, 0, 100_000 + ANIM_MS);
    expect(pos.inFlight).toBe(false);
    // And stays pooled forever after.
    const later = particlePosition(p, 0, 100_000 + ANIM_MS * 100);
    expect(later.cx).toBe(pos.cx);
    expect(later.cy).toBe(pos.cy);
  });

  it("clamps negative age (clock skew / fixture in the future) to pooled", () => {
    const p = part("gpu", 100_000);
    const pos = particlePosition(p, 0, 99_000);
    expect(pos.inFlight).toBe(false);
  });

  it("different pool slots produce different end positions", () => {
    const p0 = part("npu", 0);
    const p1 = part("npu", 0, 1);
    const e0 = particlePosition(p0, 0, ANIM_MS);
    const e1 = particlePosition(p1, 1, ANIM_MS);
    expect(e0.cx).not.toBe(e1.cx);
  });
});

describe("cascadeFlowRegion (SD-P1 motion render)", () => {
  it("tags fresh particles with the particle--in-flight class", () => {
    // ts in seconds (store multiplies by 1000) -> tsMs == nowMs -> age 0 ms.
    const ctx = makeCtx(100_000);
    ctx.store.ingest("edge-gpu", {
      _seq: "0",
      tool: "generate",
      ts: String(100_000 / 1000),
    });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain("particle--in-flight");
  });

  it("does NOT tag old particles with particle--in-flight", () => {
    // nowMs far enough past tsMs that ageMs > ANIM_MS.
    const ctx = makeCtx(100_000 + ANIM_MS * 10);
    ctx.store.ingest("edge-gpu", { _seq: "0", tool: "generate", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).not.toContain("particle--in-flight");
  });
});

// SD-P2: active-node pulse ------------------------------------------------

describe("isTierPulsing (SD-P2)", () => {
  it("returns false when the tier has never ingested", () => {
    expect(isTierPulsing(null, 100_000)).toBe(false);
  });

  it("returns true when age < PULSE_MS", () => {
    expect(isTierPulsing(100_000, 100_000 + PULSE_MS - 1)).toBe(true);
  });

  it("returns false at the boundary age === PULSE_MS (open interval upper)", () => {
    expect(isTierPulsing(100_000, 100_000 + PULSE_MS)).toBe(false);
  });

  it("returns true on negative age (future-stamped record / clock skew)", () => {
    // 'just landed' by any reasonable definition.
    expect(isTierPulsing(100_000, 50_000)).toBe(true);
  });
});

describe("cascadeFlowRegion (SD-P2 zone pulse render)", () => {
  it("renders one zone-pulse rect per stream tier (4 total), all inactive at start", () => {
    const ctx = makeCtx();
    const html = renderToString(cascadeFlowRegion.render(ctx));
    for (const tier of ["npu", "gpu", "verify", "cloud"] as const) {
      expect(html).toContain(`zone-pulse zone-pulse--${tier}`);
    }
    expect(html).not.toContain("zone-pulse--active");
  });

  it("flips a tier's pulse to active right after that tier ingests", () => {
    const ctx = makeCtx(100_000);
    ctx.store.ingest("edge-gpu", {
      _seq: "0",
      tool: "generate",
      ts: String(100_000 / 1000),
    });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    // Active class lands on the gpu pulse and only the gpu pulse.
    expect(html).toMatch(/zone-pulse zone-pulse--active zone-pulse--gpu/);
    expect(html).not.toMatch(/zone-pulse--active zone-pulse--npu/);
    expect(html).not.toMatch(/zone-pulse--active zone-pulse--verify/);
    expect(html).not.toMatch(/zone-pulse--active zone-pulse--cloud/);
  });

  it("drops the active class once age >= PULSE_MS", () => {
    const ctx = makeCtx(100_000 + PULSE_MS);
    ctx.store.ingest("edge-gpu", { _seq: "0", tool: "generate", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).not.toContain("zone-pulse--active");
  });
});
