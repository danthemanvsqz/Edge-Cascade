/**
 * The cascade-flow river -- the demo's centerpiece. The "architecture is the
 * eye candy": 5 tier zones laid out as the cascade's actual topology, with
 * static arcs showing the repair loop (verify->fail->gpu) and the escalation
 * path (verify->cap->tier3->cloud). Records arrive as colored particles
 * pooled in their tier's zone; per-tier sparklines show throughput over the
 * 60s window.
 *
 * The static topology is one SVG (rendered once into the shell). The dynamic
 * elements -- particles, sparklines, per-tier counts -- live inside ONE
 * overlay live region (`cascadeFlowRegion`). Why one region: `<vinyl-slot>`
 * is an HTML custom element and would not legally nest inside the topology
 * SVG's element namespace; the overlay-SVG sibling works around that. Phase B
 * may split into per-tier regions for finer-grained re-renders.
 */
import { h, liveRegion } from "@danthemanvsqz/vinyl";
import type { LiveRegion, VNode } from "@danthemanvsqz/vinyl";

import type { DashContext } from "./app.js";
import type { Particle, Tier } from "./store.js";
import { WINDOW_SECONDS } from "./store.js";

const TIERS: readonly Tier[] = ["npu", "gpu", "verify", "cloud"];
const VIEW_W = 800;
const VIEW_H = 400;

interface Zone {
  /** Tier this zone represents -- "tier3" is rendered but isn't on the
   * record stream (the Claude CLI doesn't write `.rec` itself), so the
   * `Tier` type stays the four .rec lanes and `tier3` is a layout-only id. */
  readonly id: Tier | "tier3";
  readonly label: string;
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

const ZONE_W = 120;
const ZONE_H = 80;
const ZONES: readonly Zone[] = [
  { id: "npu", label: "Tier 1 · NPU", x: 60, y: 50, w: ZONE_W, h: ZONE_H },
  { id: "gpu", label: "Tier 2 · GPU", x: 240, y: 50, w: ZONE_W, h: ZONE_H },
  { id: "verify", label: "verify", x: 420, y: 50, w: ZONE_W, h: ZONE_H },
  { id: "tier3", label: "Tier 3 · Claude CLI", x: 420, y: 220, w: ZONE_W, h: ZONE_H },
  { id: "cloud", label: "Tier 4 · cloud", x: 600, y: 220, w: ZONE_W, h: ZONE_H },
];

function zoneById(id: Zone["id"]): Zone {
  const z = ZONES.find((z) => z.id === id);
  if (!z) throw new Error(`unknown zone id: ${id}`);
  return z;
}

interface PathDef {
  readonly id: string;
  readonly d: string;
}

/** Static path definitions referenced by `<animateMotion href>` once Phase B
 * lands. Phase A only renders them as visible arcs. */
const PATHS: readonly PathDef[] = [
  { id: "route-to-npu", d: `M 0 ${zoneCenterY("npu")} L ${zoneById("npu").x} ${zoneCenterY("npu")}` },
  { id: "npu-to-gpu", d: pathBetween("npu", "gpu") },
  { id: "gpu-to-verify", d: pathBetween("gpu", "verify") },
  // Repair loop: verify -> gpu via an arc up over the row.
  { id: "verify-loop", d: repairLoopPath() },
  // Cap: verify -> tier-3 (straight down).
  { id: "verify-cap-to-tier3", d: capPath() },
  // Escalation: tier-3 -> cloud (rightward).
  { id: "tier3-to-cloud", d: pathBetween("tier3", "cloud") },
];

function zoneCenterY(id: Zone["id"]): number {
  const z = zoneById(id);
  return z.y + z.h / 2;
}
function pathBetween(a: Zone["id"], b: Zone["id"]): string {
  const za = zoneById(a);
  const zb = zoneById(b);
  // Horizontal connector zone edge -> zone edge.
  if (za.y === zb.y) {
    const y = za.y + za.h / 2;
    return `M ${za.x + za.w} ${y} L ${zb.x} ${y}`;
  }
  // Fallback (e.g. tier3 -> cloud, same y row): straight line edge-to-edge.
  return `M ${za.x + za.w} ${za.y + za.h / 2} L ${zb.x} ${zb.y + zb.h / 2}`;
}
function repairLoopPath(): string {
  const v = zoneById("verify");
  const g = zoneById("gpu");
  const y0 = v.y; // top edge of verify
  const yPeak = v.y - 30;
  const sx = v.x + v.w / 2;
  const ex = g.x + g.w / 2;
  return `M ${sx} ${y0} C ${sx} ${yPeak}, ${ex} ${yPeak}, ${ex} ${g.y}`;
}
function capPath(): string {
  const v = zoneById("verify");
  const t = zoneById("tier3");
  // Drop from verify's bottom to tier-3's top, same x (both at x=420 zone).
  return `M ${v.x + v.w / 2} ${v.y + v.h} L ${t.x + t.w / 2} ${t.y}`;
}

/** The static topology SVG -- rendered once into the initial paint. */
export function cascadeFlowTopology(): VNode {
  return h(
    "svg",
    {
      class: "topology",
      viewBox: `0 0 ${String(VIEW_W)} ${String(VIEW_H)}`,
      xmlns: "http://www.w3.org/2000/svg",
      "aria-label": "edge-cascade tier topology",
    },
    h(
      "g",
      { class: "paths" },
      ...PATHS.map((p) =>
        h("path", { id: p.id, class: "arc", d: p.d, fill: "none" }),
      ),
    ),
    h(
      "g",
      { class: "zones" },
      ...ZONES.map((z) =>
        h(
          "g",
          { class: `zone zone--${z.id}` },
          h("rect", {
            class: "zone-rect",
            x: String(z.x),
            y: String(z.y),
            width: String(z.w),
            height: String(z.h),
            rx: "6",
            ry: "6",
          }),
          h(
            "text",
            {
              class: "zone-label",
              x: String(z.x + z.w / 2),
              y: String(z.y + z.h + 16),
              "text-anchor": "middle",
            },
            z.label,
          ),
        ),
      ),
    ),
  );
}

/** The dynamic overlay -- one live region; re-renders on every TICK emit. */
export const cascadeFlowRegion: LiveRegion<DashContext> = liveRegion(
  "cascade-flow",
  (ctx) => overlaySvg(ctx),
);

function overlaySvg(ctx: DashContext): VNode {
  const nowMs = ctx.nowMs();
  const particles = ctx.store.particles();
  const byTier = bucketByTier(particles);
  return h(
    "svg",
    {
      class: "overlay",
      viewBox: `0 0 ${String(VIEW_W)} ${String(VIEW_H)}`,
      xmlns: "http://www.w3.org/2000/svg",
      "aria-hidden": "true",
    },
    h(
      "g",
      { class: "sparklines" },
      ...TIERS.map((tier) => sparklinePolyline(ctx, tier, nowMs)),
    ),
    h(
      "g",
      { class: "tier-stats" },
      ...TIERS.map((tier) => tierStat(byTier.get(tier) ?? [], tier)),
    ),
    h(
      "g",
      { class: "particles" },
      ...particles.flatMap((p) =>
        particleCircle(p, indexInTier(p, byTier), nowMs),
      ),
    ),
  );
}

function bucketByTier(particles: readonly Particle[]): Map<Tier, Particle[]> {
  const byTier = new Map<Tier, Particle[]>();
  for (const p of particles) {
    const arr = byTier.get(p.tier);
    if (arr) arr.push(p);
    else byTier.set(p.tier, [p]);
  }
  return byTier;
}

function indexInTier(p: Particle, byTier: Map<Tier, Particle[]>): number {
  const arr = byTier.get(p.tier);
  if (!arr) return 0;
  return arr.indexOf(p);
}

const PARTICLES_PER_ZONE = 12; // visual cap per tier-pool
/** SD-P1: how long a freshly-ingested particle takes to ride its entering arc
 * from arc-start to its final pool slot. Picked so the flow is visible (~1.5s
 * is a comfortable read) without piling up at the typical 5-50 rec/s. Exported
 * so unit tests can pin the boundary case `nowMs - tsMs === ANIM_MS`. */
export const ANIM_MS = 1500;

/** For each tier, the "entering arc" start point -- the off-zone position
 * particles glide FROM during the in-flight phase. Returns null when no
 * entering arc is modelled for the tier (currently every Tier has one;
 * future tier additions stay safe by getting an immediate pool render). */
export function enteringArcStart(tier: Tier): { x: number; y: number } | null {
  switch (tier) {
    case "npu":
      return { x: 0, y: zoneCenterY("npu") };
    case "gpu": {
      const npu = zoneById("npu");
      return { x: npu.x + npu.w, y: zoneCenterY("npu") };
    }
    case "verify": {
      const gpu = zoneById("gpu");
      return { x: gpu.x + gpu.w, y: zoneCenterY("gpu") };
    }
    case "cloud": {
      const tier3 = zoneById("tier3");
      return { x: tier3.x + tier3.w, y: zoneCenterY("tier3") };
    }
  }
}

/** Pure render geometry for a particle. `inFlight` = animating along the
 * entering arc (age < ANIM_MS); otherwise pooled at the final slot. Exported
 * so unit tests can assert the position math without booting a DashContext. */
export function particlePosition(
  p: Particle,
  idx: number,
  nowMs: number,
): { cx: number; cy: number; inFlight: boolean } {
  const z = zoneById(p.tier);
  // Final pool slot (same layout as the pre-SD-P1 static render).
  const slot = PARTICLES_PER_ZONE - 1 - idx;
  const poolCx = z.x + 8 + slot * ((z.w - 16) / (PARTICLES_PER_ZONE - 1));
  const poolCy = z.y + 14;

  const ageMs = nowMs - p.tsMs;
  // Clamp to pool render on (a) animation complete, (b) negative age (clock
  // skew or fixture timestamps in the future), (c) no entering arc modelled.
  if (ageMs >= ANIM_MS || ageMs < 0) {
    return { cx: poolCx, cy: poolCy, inFlight: false };
  }
  const start = enteringArcStart(p.tier);
  if (!start) return { cx: poolCx, cy: poolCy, inFlight: false };

  const progress = ageMs / ANIM_MS; // 0..1
  return {
    cx: start.x + (poolCx - start.x) * progress,
    cy: start.y + (poolCy - start.y) * progress,
    inFlight: true,
  };
}

function particleCircle(p: Particle, idx: number, nowMs: number): VNode[] {
  if (idx >= PARTICLES_PER_ZONE) return []; // older particles fall off-screen
  const pos = particlePosition(p, idx, nowMs);
  const inFlightCls = pos.inFlight ? " particle--in-flight" : "";
  const failCls = p.ok ? "" : " particle--fail";
  return [
    h("circle", {
      id: p.id,
      class: `particle particle--${p.tier}${failCls}${inFlightCls}`,
      cx: String(pos.cx),
      cy: String(pos.cy),
      r: "4",
    }),
  ];
}

function sparklinePolyline(
  ctx: DashContext,
  tier: Tier,
  nowMs: number,
): VNode {
  const z = zoneById(tier);
  const buckets = ctx.store.sparkline(tier, nowMs);
  const maxBucket = Math.max(1, ...buckets);
  // Plot from zone left edge to right edge, scaled into the bottom half.
  const yTop = z.y + z.h / 2 + 4;
  const yBottom = z.y + z.h - 8;
  const xs = WINDOW_SECONDS;
  const points = buckets
    .map((v, i) => {
      const x = z.x + 8 + (i / (xs - 1)) * (z.w - 16);
      const y = yBottom - (v / maxBucket) * (yBottom - yTop);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return h("polyline", {
    class: `sparkline sparkline--${tier}`,
    points,
    fill: "none",
  });
}

function tierStat(inTier: readonly Particle[], tier: Tier): VNode {
  const z = zoneById(tier);
  return h(
    "text",
    {
      class: `tier-stat tier-stat--${tier}`,
      x: String(z.x + z.w - 8),
      y: String(z.y + z.h / 2 - 4),
      "text-anchor": "end",
    },
    String(inTier.length),
  );
}
