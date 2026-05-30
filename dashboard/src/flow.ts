/**
 * The cascade-flow river -- the demo's centerpiece. "The architecture is the
 * eye candy": the graph is the *actual* Canvas chain (`cascade.topologies_canvas`)
 * rather than abstract tier blobs, so a viewer watches a real run flow
 * step-by-step through the Celery pipeline:
 *
 *     route -> draft -> draft_gate -> gpu_solve --.
 *       (npu)   (npu)    (verify)      (gpu)       | repair loop
 *                          ^------------------------'
 *                          |  cap
 *                          v
 *                        Tier 3 -> cloud
 *                        (CLI)     (cloud)
 *
 * Each node maps to a Celery task on its queue (the comments tag the queue);
 * records arriving on a tier's `.rec` lane light the node whose `(tier, tool)`
 * they match. A node is "hot" while it has ingested within HOT_MS -- a bright
 * ring + glow so it's obvious which worker is processing RIGHT NOW. When a
 * cascade Outcome lands, the whole stage flashes green (the local pipe won) or
 * red (it capped to a Tier-3 takeover).
 *
 * The static topology is one SVG (rendered once into the shell). The dynamic
 * elements -- particles, hot rings, per-node counts, the win/lose flash --
 * live inside ONE overlay live region (`cascadeFlowRegion`). Why one region:
 * `<vinyl-slot>` is an HTML custom element and would not legally nest inside
 * the topology SVG's element namespace; the overlay-SVG sibling works around
 * that.
 */
import { h, liveRegion } from "@danthemanvsqz/vinyl";
import type { LiveRegion, VNode } from "@danthemanvsqz/vinyl";

import type { DashContext } from "./app.js";
import type { LastOutcome, Particle, Tier } from "./store.js";

const VIEW_W = 800;
const VIEW_H = 400;

/** A node in the Canvas chain. `tier` selects the palette colour; `nodeForTool`
 * (below) decides which node a record lands in. "tier3" is rendered but never
 * receives a `.rec` particle (the Claude CLI doesn't write its own lane) -- it
 * lights from a `capped->tier3` Outcome instead. */
export type NodeId =
  | "route"
  | "draft"
  | "gate"
  | "gpu_solve"
  | "tier3"
  | "cloud";

interface ChainNode {
  readonly id: NodeId;
  readonly label: string;
  /** The Celery queue the task runs on -- shown as a small sublabel so the
   * "all the workers are represented" read is explicit. */
  readonly queue: string;
  /** Palette key (re-uses the four tier colours + the tier3 violet). */
  readonly tier: Tier | "tier3";
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

const NODE_W = 124;
const NODE_H = 66;
const ROW_TOP_Y = 60;
const ROW_BOT_Y = 250;

/** The chain, left-to-right. Top row is the happy path (route -> draft ->
 * gate -> gpu_solve); the bottom row is the cap escalation (Tier 3 -> cloud)
 * dropped under gate/gpu_solve so the `cap` and `tier3->cloud` arcs read
 * cleanly. Coordinates are in the 800x400 viewBox. */
const NODES: readonly ChainNode[] = [
  { id: "route", label: "route", queue: "npu", tier: "npu", x: 24, y: ROW_TOP_Y, w: NODE_W, h: NODE_H },
  { id: "draft", label: "draft", queue: "npu", tier: "npu", x: 192, y: ROW_TOP_Y, w: NODE_W, h: NODE_H },
  { id: "gate", label: "draft_gate", queue: "verify", tier: "verify", x: 360, y: ROW_TOP_Y, w: NODE_W, h: NODE_H },
  { id: "gpu_solve", label: "gpu_solve", queue: "gpu", tier: "gpu", x: 528, y: ROW_TOP_Y, w: NODE_W, h: NODE_H },
  { id: "tier3", label: "Tier 3 · CLI", queue: "—", tier: "tier3", x: 360, y: ROW_BOT_Y, w: NODE_W, h: NODE_H },
  { id: "cloud", label: "cloud", queue: "cloud", tier: "cloud", x: 528, y: ROW_BOT_Y, w: NODE_W, h: NODE_H },
];

/** Nodes that pool real `.rec` particles, in pipeline order. tier3 is excluded
 * (no lane); it lights from the Outcome flash only. */
const PARTICLE_NODES: readonly NodeId[] = [
  "route",
  "draft",
  "gate",
  "gpu_solve",
  "cloud",
];

function nodeById(id: NodeId): ChainNode {
  const n = NODES.find((n) => n.id === id);
  if (!n) throw new Error(`unknown node id: ${id}`);
  return n;
}

/** Which chain node a record belongs to, from its tier + tool. The NPU lane
 * carries both `route` and `draft`; everything else is one node per tier.
 * Status/unknown tools fall back to the tier's primary node so a `tool=status`
 * heartbeat still lights the right worker. Exported for unit tests. */
export function nodeForParticle(p: Particle): NodeId {
  switch (p.tier) {
    case "npu":
      return p.tool === "route" ? "route" : "draft";
    case "verify":
      return "gate";
    case "gpu":
      return "gpu_solve";
    case "cloud":
      return "cloud";
  }
}

interface PathDef {
  readonly id: string;
  readonly d: string;
  /** repair/cap arcs render dashed + dimmer than the forward flow. */
  readonly kind: "flow" | "repair" | "cap";
}

const PATHS: readonly PathDef[] = [
  { id: "entry-route", d: entryPath("route"), kind: "flow" },
  { id: "route-draft", d: pathBetween("route", "draft"), kind: "flow" },
  { id: "draft-gate", d: pathBetween("draft", "gate"), kind: "flow" },
  { id: "gate-gpu", d: pathBetween("gate", "gpu_solve"), kind: "flow" },
  // Bounded repair loop: gpu_solve sends the failed draft back to the gate.
  { id: "repair-loop", d: repairLoopPath(), kind: "repair" },
  // Cap: gate gives up (repair exhausted) -> Tier 3 takeover, straight down.
  { id: "cap-tier3", d: capPath(), kind: "cap" },
  // Escalation: Tier 3 -> cloud (only when cloud is wired + budget allows).
  { id: "tier3-cloud", d: pathBetween("tier3", "cloud"), kind: "cap" },
];

function nodeCenterY(id: NodeId): number {
  const n = nodeById(id);
  return n.y + n.h / 2;
}
function nodeCenterX(id: NodeId): number {
  const n = nodeById(id);
  return n.x + n.w / 2;
}
function entryPath(id: NodeId): string {
  const y = nodeCenterY(id);
  return `M 0 ${String(y)} L ${String(nodeById(id).x)} ${String(y)}`;
}
function pathBetween(a: NodeId, b: NodeId): string {
  const na = nodeById(a);
  const nb = nodeById(b);
  const y = na.y + na.h / 2;
  if (na.y === nb.y) {
    return `M ${String(na.x + na.w)} ${String(y)} L ${String(nb.x)} ${String(y)}`;
  }
  return `M ${String(na.x + na.w)} ${String(y)} L ${String(nb.x)} ${String(nb.y + nb.h / 2)}`;
}
function repairLoopPath(): string {
  const sx = nodeCenterX("gpu_solve");
  const ex = nodeCenterX("gate");
  const y0 = ROW_TOP_Y; // top edge of the row
  const yPeak = ROW_TOP_Y - 34;
  return `M ${String(sx)} ${String(y0)} C ${String(sx)} ${String(yPeak)}, ${String(ex)} ${String(yPeak)}, ${String(ex)} ${String(y0)}`;
}
function capPath(): string {
  const x = nodeCenterX("gate");
  const y0 = nodeById("gate").y + nodeById("gate").h;
  const y1 = nodeById("tier3").y;
  return `M ${String(x)} ${String(y0)} L ${String(x)} ${String(y1)}`;
}

/** The static topology SVG -- rendered once into the initial paint. */
export function cascadeFlowTopology(): VNode {
  return h(
    "svg",
    {
      class: "topology",
      viewBox: `0 0 ${String(VIEW_W)} ${String(VIEW_H)}`,
      xmlns: "http://www.w3.org/2000/svg",
      "aria-label": "edge-cascade Canvas chain topology",
    },
    h(
      "g",
      { class: "paths" },
      ...PATHS.map((p) =>
        h("path", {
          id: p.id,
          class: `arc arc--${p.kind}`,
          d: p.d,
          fill: "none",
        }),
      ),
    ),
    h(
      "g",
      { class: "nodes" },
      ...NODES.map((n) =>
        h(
          "g",
          { class: `node node--${n.id} node--tone-${n.tier}` },
          h("rect", {
            class: "node-rect",
            x: String(n.x),
            y: String(n.y),
            width: String(n.w),
            height: String(n.h),
            rx: "7",
            ry: "7",
          }),
          h(
            "text",
            {
              class: "node-label",
              x: String(n.x + n.w / 2),
              y: String(n.y + n.h / 2 + 1),
              "text-anchor": "middle",
            },
            n.label,
          ),
          h(
            "text",
            {
              class: "node-queue",
              x: String(n.x + n.w / 2),
              y: String(n.y + n.h - 10),
              "text-anchor": "middle",
            },
            n.queue === "—" ? "tier 3" : `Q:${n.queue}`,
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
  const byNode = bucketByNode(particles);
  const lastOutcome = ctx.store.lastOutcome();
  return h(
    "svg",
    {
      class: "overlay",
      viewBox: `0 0 ${String(VIEW_W)} ${String(VIEW_H)}`,
      xmlns: "http://www.w3.org/2000/svg",
      "aria-hidden": "true",
    },
    outcomeFlash(lastOutcome, nowMs),
    h(
      "g",
      { class: "node-hots" },
      ...NODES.map((n) => hotRing(n, byNode, lastOutcome, nowMs)),
    ),
    h(
      "g",
      { class: "node-stats" },
      ...PARTICLE_NODES.map((id) => nodeStat(byNode.get(id) ?? [], id)),
    ),
    h(
      "g",
      { class: "particles" },
      ...particles.flatMap((p) =>
        particleCircle(p, indexInNode(p, byNode), nowMs),
      ),
    ),
    outcomeBanner(lastOutcome, nowMs),
  );
}

function bucketByNode(particles: readonly Particle[]): Map<NodeId, Particle[]> {
  const byNode = new Map<NodeId, Particle[]>();
  for (const p of particles) {
    const id = nodeForParticle(p);
    const arr = byNode.get(id);
    if (arr) arr.push(p);
    else byNode.set(id, [p]);
  }
  return byNode;
}

function indexInNode(p: Particle, byNode: Map<NodeId, Particle[]>): number {
  const arr = byNode.get(nodeForParticle(p));
  if (!arr) return 0;
  return arr.indexOf(p);
}

const PARTICLES_PER_NODE = 12; // visual cap per node-pool
/** SD-P1: how long a freshly-ingested particle takes to ride its entering arc
 * from arc-start to its final pool slot. Exported so unit tests can pin the
 * boundary case `nowMs - tsMs === ANIM_MS`. */
export const ANIM_MS = 1500;

/** For each particle node, the "entering arc" start point -- the off-node
 * position particles glide FROM during the in-flight phase. It is the right
 * edge of the pipeline predecessor (route enters from off-canvas; cloud enters
 * from Tier 3). Exported for unit tests. */
export function enteringArcStart(id: NodeId): { x: number; y: number } | null {
  switch (id) {
    case "route":
      return { x: 0, y: nodeCenterY("route") };
    case "draft": {
      const prev = nodeById("route");
      return { x: prev.x + prev.w, y: nodeCenterY("route") };
    }
    case "gate": {
      const prev = nodeById("draft");
      return { x: prev.x + prev.w, y: nodeCenterY("draft") };
    }
    case "gpu_solve": {
      const prev = nodeById("gate");
      return { x: prev.x + prev.w, y: nodeCenterY("gate") };
    }
    case "cloud": {
      const prev = nodeById("tier3");
      return { x: prev.x + prev.w, y: nodeCenterY("tier3") };
    }
    case "tier3":
      return null; // tier3 never pools particles
  }
}

/** Pure render geometry for a particle. `inFlight` = animating along the
 * entering arc (age < ANIM_MS); otherwise pooled at its final slot. Exported
 * so unit tests can assert the position math without booting a DashContext. */
export function particlePosition(
  p: Particle,
  idx: number,
  nowMs: number,
): { cx: number; cy: number; inFlight: boolean } {
  const id = nodeForParticle(p);
  const n = nodeById(id);
  const slot = PARTICLES_PER_NODE - 1 - idx;
  const poolCx = n.x + 8 + slot * ((n.w - 16) / (PARTICLES_PER_NODE - 1));
  const poolCy = n.y + 13;

  const ageMs = nowMs - p.tsMs;
  if (ageMs >= ANIM_MS || ageMs < 0) {
    return { cx: poolCx, cy: poolCy, inFlight: false };
  }
  const start = enteringArcStart(id);
  if (!start) return { cx: poolCx, cy: poolCy, inFlight: false };

  const progress = ageMs / ANIM_MS;
  return {
    cx: start.x + (poolCx - start.x) * progress,
    cy: start.y + (poolCy - start.y) * progress,
    inFlight: true,
  };
}

function particleCircle(p: Particle, idx: number, nowMs: number): VNode[] {
  if (idx >= PARTICLES_PER_NODE) return []; // older particles fall off-screen
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

/** How long after ingest a node stays "hot" (bright ring + glow + fill).
 * Longer than the particle-arc ANIM_MS so a node clearly reads as "spinning"
 * for a beat after a record lands -- a single record gives a ~2.6s glow and a
 * steady stream reads as sustained. Exported for unit tests pinning the
 * boundary. (Note: .rec records land at task COMPLETION, so this is "recently
 * active", the closest proxy the record stream offers to "in progress".) */
export const HOT_MS = 2600;

/** Is this node currently hot? A node is hot iff its most-recent matching
 * particle landed within HOT_MS. tier3 has no lane, so it goes hot from a
 * `capped->tier3` Outcome within HOT_MS instead. Pure -- no DOM. Exported. */
export function isNodeHot(
  id: NodeId,
  byNode: Map<NodeId, Particle[]>,
  lastOutcome: LastOutcome | null,
  nowMs: number,
): boolean {
  if (id === "tier3") {
    if (lastOutcome === null || lastOutcome.finalTier !== "capped->tier3") {
      return false;
    }
    const age = nowMs - lastOutcome.tsMs;
    return age >= 0 ? age < HOT_MS : true;
  }
  const arr = byNode.get(id);
  if (!arr || arr.length === 0) return false;
  // Particles are appended in arrival order; the last is the newest.
  const newest = arr[arr.length - 1]!;
  const age = nowMs - newest.tsMs;
  if (age < 0) return true; // future-stamped record just landed
  return age < HOT_MS;
}

/** Per-node hot ring overlay. Always rendered (one rect per node); the
 * `--hot` class flips on while the node is processing so CSS keyframes run a
 * single sustained glow without re-triggering on every render. */
function hotRing(
  n: ChainNode,
  byNode: Map<NodeId, Particle[]>,
  lastOutcome: LastOutcome | null,
  nowMs: number,
): VNode {
  const hot = isNodeHot(n.id, byNode, lastOutcome, nowMs);
  const cls = hot
    ? `node-hot node-hot--hot node-hot--tone-${n.tier}`
    : `node-hot node-hot--tone-${n.tier}`;
  return h("rect", {
    class: cls,
    x: String(n.x - 3),
    y: String(n.y - 3),
    width: String(n.w + 6),
    height: String(n.h + 6),
    rx: "9",
    ry: "9",
    fill: "none",
    "aria-hidden": "true",
  });
}

function nodeStat(inNode: readonly Particle[], id: NodeId): VNode {
  const n = nodeById(id);
  return h(
    "text",
    {
      class: `node-stat node-stat--${n.tier}`,
      x: String(n.x + n.w - 8),
      y: String(n.y + 16),
      "text-anchor": "end",
    },
    String(inNode.length),
  );
}

/** How long the win/lose flash stays up after an Outcome lands. Exported so
 * the heartbeat (app.ts) keeps ticking through the whole flash. */
export const FLASH_MS = 1600;

/** True while the most-recent Outcome's flash window is still open. Pure;
 * shared by the overlay render and the heartbeat so they agree on the window. */
export function isFlashing(
  lastOutcome: LastOutcome | null,
  nowMs: number,
): boolean {
  if (lastOutcome === null) return false;
  const age = nowMs - lastOutcome.tsMs;
  return age >= 0 && age < FLASH_MS;
}

/** Full-stage green/red wash on a fresh Outcome. Rendered first (behind the
 * nodes/particles) so it tints the whole graph without hiding the flow. */
function outcomeFlash(lastOutcome: LastOutcome | null, nowMs: number): VNode {
  if (!isFlashing(lastOutcome, nowMs)) {
    return h("g", { class: "outcome-flash" });
  }
  const tone = lastOutcome!.won ? "win" : "lose";
  return h(
    "g",
    { class: `outcome-flash outcome-flash--active outcome-flash--${tone}` },
    h("rect", {
      class: "outcome-flash-rect",
      x: "0",
      y: "0",
      width: String(VIEW_W),
      height: String(VIEW_H),
      "aria-hidden": "true",
    }),
  );
}

/** The big WIN/LOSE word that punches in over the flash. */
function outcomeBanner(lastOutcome: LastOutcome | null, nowMs: number): VNode {
  if (!isFlashing(lastOutcome, nowMs)) {
    return h("g", { class: "outcome-banner" });
  }
  const o = lastOutcome!;
  const tone = o.won ? "win" : "lose";
  const word = o.won ? "LOCAL WIN" : "CAPPED";
  const sub = o.won ? `resolved @ ${o.finalTier}` : "→ Tier 3 takeover";
  return h(
    "g",
    { class: `outcome-banner outcome-banner--active outcome-banner--${tone}` },
    h(
      "text",
      {
        class: "outcome-banner-word",
        x: String(VIEW_W / 2),
        y: String(VIEW_H / 2 - 6),
        "text-anchor": "middle",
      },
      word,
    ),
    h(
      "text",
      {
        class: "outcome-banner-sub",
        x: String(VIEW_W / 2),
        y: String(VIEW_H / 2 + 20),
        "text-anchor": "middle",
      },
      sub,
    ),
  );
}

/** SD-P3 heartbeat: how often the server re-emits TICK while animations are
 * still running. ~12 Hz -- smooth motion + glow fades without spamming WS
 * frames. When nothing is animating the chain stops, so an idle dashboard
 * issues zero ticks. Exported for app.ts to schedule against and tests to pin. */
export const HEARTBEAT_MS = 80;

/** Does the current store state warrant another TICK before any record
 * arrives? True iff a particle is still mid-arc (SD-P1), a node is still hot
 * (HOT_MS), OR a win/lose flash is still up (FLASH_MS). Pure -- app.ts composes
 * it into a setTimeout chain; tests drive it deterministically.
 *
 * Each branch requires `age >= 0`: a far-future timestamp must not pin the
 * heartbeat on for (future - now) ms. */
export function hasActiveAnimation(
  particles: readonly Particle[],
  lastOutcome: LastOutcome | null,
  nowMs: number,
): boolean {
  for (const p of particles) {
    const age = nowMs - p.tsMs;
    if (age >= 0 && age < ANIM_MS) return true;
    if (age >= 0 && age < HOT_MS) return true;
  }
  return isFlashing(lastOutcome, nowMs);
}
