import { describe, expect, it } from "vitest";
import { renderToString } from "@danthemanvsqz/vinyl";

import type { DashContext } from "../src/app.js";
import {
  ANIM_MS,
  cascadeFlowRegion,
  cascadeFlowTopology,
  enteringArcStart,
  FLASH_MS,
  hasActiveAnimation,
  HEARTBEAT_MS,
  HOT_MS,
  isFlashing,
  isNodeHot,
  nodeForParticle,
  particlePosition,
} from "../src/flow.js";
import type { NodeId } from "../src/flow.js";
import { createStore } from "../src/store.js";
import type { LastOutcome, Particle, Tier } from "../src/store.js";

function makeCtx(nowMs = 100_000): DashContext {
  return {
    store: createStore(),
    // The hub isn't used by the topology/overlay -- regions read the store
    // synchronously. A typed undefined cast keeps the test cheap.
    hub: undefined as unknown as DashContext["hub"],
    nowMs: () => nowMs,
  };
}

function part(tier: Tier, tsMs: number, seq = 0, tool = "anything"): Particle {
  return {
    id: `p-edge-${tier}-${seq}`,
    tier,
    server: `edge-${tier}`,
    seq: String(seq),
    tool,
    tsMs,
    latencyMs: 0,
    ok: true,
  };
}

function outcome(finalTier: string, tsMs: number, won: boolean): LastOutcome {
  return { seq: 1, tsMs, finalTier, won };
}

describe("cascadeFlowTopology (static Canvas chain)", () => {
  it("renders an 800x400 SVG with the chain node labels and the chain arcs", () => {
    const html = renderToString(cascadeFlowTopology());
    expect(html).toContain('viewBox="0 0 800 400"');
    // Every chain node label appears -- the real Celery tasks, not abstract
    // tier blobs.
    for (const label of [
      "route",
      "draft",
      "draft_gate",
      "gpu_solve",
      "Tier 3 · CLI",
      "cloud",
    ]) {
      expect(html).toContain(label);
    }
    // The queue sublabels make "all the workers are represented" explicit.
    for (const q of ["Q:npu", "Q:verify", "Q:gpu", "Q:cloud"]) {
      expect(html).toContain(q);
    }
    // Forward-flow + repair + cap arcs each present, by id.
    for (const pathId of [
      "entry-route",
      "route-draft",
      "draft-gate",
      "gate-gpu",
      "repair-loop",
      "cap-tier3",
      "tier3-cloud",
    ]) {
      expect(html).toContain(`id="${pathId}"`);
    }
    // Styling hooks.
    expect(html).toContain('class="node-rect"');
    expect(html).toContain("node--route");
    expect(html).toContain("node--tone-npu");
    expect(html).toContain("node--tier3");
    // Repair + cap arcs are visually distinguished from forward flow.
    expect(html).toContain("arc--flow");
    expect(html).toContain("arc--repair");
    expect(html).toContain("arc--cap");
  });
});

describe("nodeForParticle (tier+tool -> chain node)", () => {
  it("splits the NPU lane into route vs draft by tool", () => {
    expect(nodeForParticle(part("npu", 0, 0, "route"))).toBe("route");
    expect(nodeForParticle(part("npu", 0, 0, "draft"))).toBe("draft");
  });
  it("maps a non-route NPU tool (e.g. status) to draft", () => {
    expect(nodeForParticle(part("npu", 0, 0, "status"))).toBe("draft");
  });
  it("maps verify->gate, gpu->gpu_solve, cloud->cloud", () => {
    expect(nodeForParticle(part("verify", 0))).toBe("gate");
    expect(nodeForParticle(part("gpu", 0))).toBe("gpu_solve");
    expect(nodeForParticle(part("cloud", 0))).toBe("cloud");
  });
});

describe("hasActiveAnimation (heartbeat predicate)", () => {
  it("returns false when nothing has happened (idle issues zero ticks)", () => {
    expect(hasActiveAnimation([], null, 100_000)).toBe(false);
  });

  it("returns true while a particle is mid-arc (age < ANIM_MS)", () => {
    const p = part("npu", 100_000);
    expect(hasActiveAnimation([p], null, 100_000 + ANIM_MS - 1)).toBe(true);
  });

  it("returns true while a node is still hot, after the arc has settled", () => {
    // HOT_MS > ANIM_MS, so there's a window where the particle has pooled
    // (arc done) but the node is still hot -- the hot branch must keep the
    // heartbeat alive there, independently of the arc branch.
    const p = part("npu", 100_000);
    expect(ANIM_MS).toBeLessThan(HOT_MS); // contract this test relies on
    expect(hasActiveAnimation([p], null, 100_000 + ANIM_MS + 1)).toBe(true);
    expect(hasActiveAnimation([p], null, 100_000 + HOT_MS - 1)).toBe(true);
  });

  it("returns false once a particle has finished both arc and hot windows", () => {
    const p = part("npu", 100_000);
    const past = 100_000 + Math.max(ANIM_MS, HOT_MS);
    expect(hasActiveAnimation([p], null, past)).toBe(false);
  });

  it("returns true while a win/lose flash is still up (age < FLASH_MS)", () => {
    const o = outcome("gpu", 100_000, true);
    expect(hasActiveAnimation([], o, 100_000 + FLASH_MS - 1)).toBe(true);
  });

  it("returns false once the flash window has closed", () => {
    const o = outcome("gpu", 100_000, true);
    expect(hasActiveAnimation([], o, 100_000 + FLASH_MS)).toBe(false);
  });

  it("stays alive for the tier3 hot window on a capped->tier3 (HOT_MS > FLASH_MS)", () => {
    // tier3's ring glows for HOT_MS from the outcome, which outlasts the flash;
    // the heartbeat must cover it or the ring freezes mid-glow.
    const o = outcome("capped->tier3", 100_000, false);
    expect(FLASH_MS).toBeLessThan(HOT_MS);
    expect(hasActiveAnimation([], o, 100_000 + FLASH_MS + 1)).toBe(true);
    expect(hasActiveAnimation([], o, 100_000 + HOT_MS)).toBe(false);
  });

  it("ignores future-stamped particles (clock skew can't pin the chain)", () => {
    const future = part("npu", 200_000);
    expect(hasActiveAnimation([future], null, 100_000)).toBe(false);
  });

  it("HEARTBEAT_MS is a small positive number (not zero, not huge)", () => {
    expect(HEARTBEAT_MS).toBeGreaterThan(0);
    expect(HEARTBEAT_MS).toBeLessThan(500);
  });
});

describe("isNodeHot (per-node hot indicator)", () => {
  function byNode(particles: Particle[]): Map<NodeId, Particle[]> {
    const m = new Map<NodeId, Particle[]>();
    for (const p of particles) {
      const id = nodeForParticle(p);
      const arr = m.get(id);
      if (arr) arr.push(p);
      else m.set(id, [p]);
    }
    return m;
  }

  it("is false for a node with no particles", () => {
    expect(isNodeHot("gpu_solve", byNode([]), null, 100_000)).toBe(false);
  });

  it("is true while a matching particle is within HOT_MS", () => {
    const m = byNode([part("gpu", 100_000)]);
    expect(isNodeHot("gpu_solve", m, null, 100_000 + HOT_MS - 1)).toBe(true);
  });

  it("drops to false at the HOT_MS boundary", () => {
    const m = byNode([part("gpu", 100_000)]);
    expect(isNodeHot("gpu_solve", m, null, 100_000 + HOT_MS)).toBe(false);
  });

  it("tier3 goes hot from a capped->tier3 outcome (no .rec lane)", () => {
    const o = outcome("capped->tier3", 100_000, false);
    expect(isNodeHot("tier3", new Map(), o, 100_000 + HOT_MS - 1)).toBe(true);
  });

  it("tier3 stays cold for a local-win outcome", () => {
    const o = outcome("gpu", 100_000, true);
    expect(isNodeHot("tier3", new Map(), o, 100_000)).toBe(false);
  });
});

describe("isFlashing / FLASH_MS", () => {
  it("is false with no outcome", () => {
    expect(isFlashing(null, 100_000)).toBe(false);
  });
  it("is true inside the window, false at the boundary", () => {
    const o = outcome("gpu", 100_000, true);
    expect(isFlashing(o, 100_000 + FLASH_MS - 1)).toBe(true);
    expect(isFlashing(o, 100_000 + FLASH_MS)).toBe(false);
  });
  it("ignores future-stamped outcomes", () => {
    const o = outcome("gpu", 200_000, true);
    expect(isFlashing(o, 100_000)).toBe(false);
  });
});

describe("enteringArcStart (particle motion origin)", () => {
  it("returns a start for each particle node, null for tier3", () => {
    for (const id of ["route", "draft", "gate", "gpu_solve", "cloud"] as const) {
      expect(enteringArcStart(id)).not.toBeNull();
    }
    expect(enteringArcStart("tier3")).toBeNull();
  });
  it("route starts at x=0 (off the left edge)", () => {
    expect(enteringArcStart("route")?.x).toBe(0);
  });
});

describe("particlePosition (motion geometry)", () => {
  it("places a freshly-ingested particle at the arc start (progress=0)", () => {
    const p = part("gpu", 100_000);
    const pos = particlePosition(p, 0, 100_000);
    const start = enteringArcStart("gpu_solve");
    expect(start).not.toBeNull();
    expect(pos.cx).toBeCloseTo(start!.x, 5);
    expect(pos.cy).toBeCloseTo(start!.y, 5);
    expect(pos.inFlight).toBe(true);
  });

  it("interpolates linearly to the pool slot at progress=0.5", () => {
    const p = part("gpu", 100_000);
    const mid = particlePosition(p, 0, 100_000 + ANIM_MS / 2);
    const startPos = particlePosition(p, 0, 100_000);
    const endPos = particlePosition(p, 0, 100_000 + ANIM_MS);
    expect(mid.cx).toBeCloseTo((startPos.cx + endPos.cx) / 2, 4);
    expect(mid.cy).toBeCloseTo((startPos.cy + endPos.cy) / 2, 4);
    expect(mid.inFlight).toBe(true);
  });

  it("pools (inFlight false) once age >= ANIM_MS and stays put", () => {
    const p = part("gpu", 100_000);
    const pos = particlePosition(p, 0, 100_000 + ANIM_MS);
    expect(pos.inFlight).toBe(false);
    const later = particlePosition(p, 0, 100_000 + ANIM_MS * 100);
    expect(later.cx).toBe(pos.cx);
    expect(later.cy).toBe(pos.cy);
  });

  it("clamps negative age to pooled", () => {
    const pos = particlePosition(part("gpu", 100_000), 0, 99_000);
    expect(pos.inFlight).toBe(false);
  });

  it("different pool slots produce different end positions", () => {
    const e0 = particlePosition(part("npu", 0, 0, "route"), 0, ANIM_MS);
    const e1 = particlePosition(part("npu", 0, 1, "route"), 1, ANIM_MS);
    expect(e0.cx).not.toBe(e1.cx);
  });
});

describe("cascadeFlowRegion (overlay live region)", () => {
  it("renders an empty overlay with a hot ring per node and no particles", () => {
    const ctx = makeCtx();
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain('class="overlay"');
    // One hot ring per node (6); none active at rest.
    expect(html.match(/class="node-hot /g)?.length ?? 0).toBe(6);
    expect(html).not.toContain("node-hot--hot");
    // No particles, no flash.
    expect(html.match(/class="particle particle--/g)).toBeNull();
    expect(html).not.toContain("outcome-flash--active");
  });

  it("emits one particle per ingested record routed to the right node tone", () => {
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
    expect(html).toContain("particle particle--npu");
    expect(html).toContain("particle particle--gpu");
    expect(html).toContain("particle particle--verify particle--fail");
  });

  it("caps visible particles per node at PARTICLES_PER_NODE (12)", () => {
    const ctx = makeCtx();
    for (let i = 0; i < 30; i++) {
      ctx.store.ingest("edge-npu", { _seq: String(i), tool: "route", ts: "100" });
    }
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect((html.match(/particle--npu/g) ?? []).length).toBe(12);
  });

  it("flips a node's hot ring to --hot right after it ingests", () => {
    const ctx = makeCtx(100_000);
    ctx.store.ingest("edge-gpu", {
      _seq: "0",
      tool: "generate",
      ts: String(100_000 / 1000),
    });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain("node-hot--hot");
  });

  it("tags fresh particles with particle--in-flight, old ones not", () => {
    const fresh = makeCtx(100_000);
    fresh.store.ingest("edge-gpu", {
      _seq: "0",
      tool: "generate",
      ts: String(100_000 / 1000),
    });
    expect(renderToString(cascadeFlowRegion.render(fresh))).toContain(
      "particle--in-flight",
    );

    const old = makeCtx(100_000 + ANIM_MS * 10);
    old.store.ingest("edge-gpu", { _seq: "0", tool: "generate", ts: "100" });
    expect(renderToString(cascadeFlowRegion.render(old))).not.toContain(
      "particle--in-flight",
    );
  });

  it("renders a green WIN flash on a local-win outcome", () => {
    const ctx = makeCtx(100_000);
    ctx.store.ingest("cascade", { final_tier: "gpu", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain("outcome-flash--active outcome-flash--win");
    expect(html).toContain("LOCAL WIN");
  });

  it("renders a red LOSE flash on a capped->tier3 outcome", () => {
    const ctx = makeCtx(100_000);
    ctx.store.ingest("cascade", { final_tier: "capped->tier3", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).toContain("outcome-flash--active outcome-flash--lose");
    expect(html).toContain("CAPPED");
  });

  it("drops the flash once FLASH_MS has elapsed", () => {
    const ctx = makeCtx(100_000 + FLASH_MS);
    ctx.store.ingest("cascade", { final_tier: "gpu", ts: "100" });
    const html = renderToString(cascadeFlowRegion.render(ctx));
    expect(html).not.toContain("outcome-flash--active");
  });
});
