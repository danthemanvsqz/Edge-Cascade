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
  /** Tier this zone represents. "tier3" is rendered but isn't on the
   * record stream (the Claude CLI doesn't write `.rec` itself); "igpu" is
   * rendered as the optional Tier-1b sibling drafter but currently has no
   * dedicated `.rec` lane either (its MCP calls land in `edge-npu.rec`).
   * Both are layout-only ids -- the `Tier` type stays the four .rec lanes
   * that produce particles, so adding zones here doesn't expand the store. */
  readonly id: Tier | "tier3" | "igpu";
  readonly label: string;
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

const ZONE_W = 120;
const ZONE_H = 80;
// Tier-1 column (NPU + iGPU as sibling drafters) uses a slimmer height so the
// two stack vertically within the same horizontal band. GPU + verify are
// vertically centred against the pair so the horizontal arcs read cleanly.
const TIER1_H = 60;
const ZONES: readonly Zone[] = [
  // Tier 1: NPU (top) + iGPU (bottom) -- parallel drafters; cascade.mesh.solve
  // can pick either (or both, if the topology wires igpu_draft). iGPU is the
  // optional Tier-1b sibling counted in SD-4's effectiveness panel and the
  // SD-2b degen panel; before this change it had no representation in the
  // flow graph, so a real iGPU win was invisible. Same Tier-1 colour band so
  // the visual hierarchy still reads "Tier 1" as a unit.
  { id: "npu", label: "Tier 1 · NPU", x: 60, y: 30, w: ZONE_W, h: TIER1_H },
  { id: "igpu", label: "Tier 1b · iGPU", x: 60, y: 110, w: ZONE_W, h: TIER1_H },
  // GPU + verify vertically centred against the NPU+iGPU pair (NPU top=30,
  // iGPU bottom=170, midpoint=100; ZONE_H/2=40 -> y=60).
  { id: "gpu", label: "Tier 2 · GPU", x: 240, y: 60, w: ZONE_W, h: ZONE_H },
  { id: "verify", label: "verify", x: 420, y: 60, w: ZONE_W, h: ZONE_H },
  { id: "tier3", label: "Tier 3 · Claude CLI", x: 420, y: 230, w: ZONE_W, h: ZONE_H },
  { id: "cloud", label: "Tier 4 · cloud", x: 600, y: 230, w: ZONE_W, h: ZONE_H },
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
  // Entry from off-canvas (route) to the two Tier-1 drafters in parallel.
  // cascade.mesh.solve picks NPU as the primary draft path; iGPU is the
  // optional sibling drafter when the topology wires `igpu_draft`.
  { id: "route-to-npu", d: `M 0 ${zoneCenterY("npu")} L ${zoneById("npu").x} ${zoneCenterY("npu")}` },
  { id: "route-to-igpu", d: `M 0 ${zoneCenterY("igpu")} L ${zoneById("igpu").x} ${zoneCenterY("igpu")}` },
  // Both Tier-1 drafters feed Tier-2 GPU (the repair-loop driver). NPU comes
  // in from above-center, iGPU from below-center; pathBetween handles the
  // diagonal edge-to-edge line.
  { id: "npu-to-gpu", d: pathBetween("npu", "gpu") },
  { id: "igpu-to-gpu", d: pathBetween("igpu", "gpu") },
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
      { class: "zone-pulses" },
      ...TIERS.map((tier) => zonePulse(ctx, tier, nowMs)),
    ),
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

/** SD-P2: how long the zone stays in the "active" visual state after a tier
 * ingests a record. Picked so a steady stream at 5+ rec/s reads as a
 * sustained pulse, while a single isolated record gives a clear 1-2s ping
 * before dimming. Exported for unit tests pinning the boundary. */
export const PULSE_MS = 1200;

/** SD-P2: is this tier currently in the active-pulse window? Pure helper
 * (no DOM) -- pulled out so tests can pin boundary cases without rendering. */
export function isTierPulsing(
  lastIngestMs: number | null,
  nowMs: number,
): boolean {
  if (lastIngestMs === null) return false;
  const age = nowMs - lastIngestMs;
  // Defensive: a future-stamped record (clock skew / fixture in the future)
  // counts as active -- it just landed, by any reasonable definition.
  if (age < 0) return true;
  return age < PULSE_MS;
}

/** SD-P3 heartbeat: how often the server re-emits TICK while animations are
 * still running. Picked at ~12 Hz -- visibly smooth on both motion (SD-P1)
 * and pulse-fade (SD-P2) without spamming WS frames. When no animation is
 * active the chain stops naturally, so an idle dashboard issues zero ticks.
 * Exported for app.ts to schedule against and for tests to pin. */
export const HEARTBEAT_MS = 80;

/** SD-P3: does the current store state warrant another TICK before any
 * record arrives? True iff EITHER an SD-P1 particle is still mid-arc
 * (age < ANIM_MS) OR an SD-P2 zone is still in its pulse window
 * (age < PULSE_MS). Pure -- no DOM, no time source beyond `nowMs` -- so
 * app.ts can compose it into a setTimeout chain without owning the
 * animation constants, and tests can drive it deterministically.
 *
 * The lookup callback shape mirrors `store.lastIngestMs` exactly so the
 * caller hands the bound method through without a per-tier closure. */
export function hasActiveAnimation(
  particles: readonly Particle[],
  lastIngestMs: (tier: Tier) => number | null,
  nowMs: number,
): boolean {
  for (const p of particles) {
    const age = nowMs - p.tsMs;
    if (age >= 0 && age < ANIM_MS) return true;
  }
  for (const tier of TIERS) {
    if (isTierPulsing(lastIngestMs(tier), nowMs)) return true;
  }
  return false;
}

/** Per-zone pulse overlay. Always rendered (one rect per tier zone); the
 * `--active` class flips on when the tier has ingested in the last
 * PULSE_MS, so CSS keyframes can run a single short pulse without re-
 * triggering on every re-render. */
function zonePulse(ctx: DashContext, tier: Tier, nowMs: number): VNode {
  const z = zoneById(tier);
  const last = ctx.store.lastIngestMs(tier);
  const active = isTierPulsing(last, nowMs);
  const cls = active
    ? `zone-pulse zone-pulse--active zone-pulse--${tier}`
    : `zone-pulse zone-pulse--${tier}`;
  return h("rect", {
    class: cls,
    x: String(z.x - 2),
    y: String(z.y - 2),
    width: String(z.w + 4),
    height: String(z.h + 4),
    rx: "8",
    ry: "8",
    fill: "none",
    "aria-hidden": "true",
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
