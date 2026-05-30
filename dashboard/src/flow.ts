/**
 * The cascade-flow river -- the demo's centerpiece. "The architecture is the
 * eye candy": the graph is the *actual* Canvas chain (`cascade.topologies_canvas`)
 * rather than abstract tier blobs, so a viewer watches a real run flow
 * step-by-step through the Celery pipeline.
 *
 * THE TOPOLOGY IS DATA-DRIVEN. Add a task to CHAIN_SPECS (one line), restart
 * the server (`npm run dev` auto-restarts), and positions, paths, and node
 * count all update automatically -- no coordinate editing required. The SVG
 * uses CSS `width: 100%` so it scales to fit any container regardless of chain
 * length.
 *
 * For experiments that change the topology frequently, call `setTopology()` at
 * server startup with the set of Celery task names currently registered on the
 * worker (query Flower's `/api/workers`). Only tasks in that set are shown;
 * synthetic nodes (tier3, cloud) always appear. Falls back to the full
 * CHAIN_SPECS if Flower is unavailable.
 *
 * DYNAMIC ELEMENTS: particles, hot rings, per-node counts, the win/lose flash
 * live inside ONE overlay live region (`cascadeFlowRegion`). The spinning ring
 * is a separate live region (`cascadeSpinRegion`) so its CSS animation runs
 * uninterrupted between ledger re-renders.
 */
import { h, liveRegion } from "@danthemanvsqz/vinyl";
import type { LiveRegion, VNode } from "@danthemanvsqz/vinyl";

import type { DashContext } from "./app.js";
import type { LastOutcome, Particle, Tier } from "./store.js";

// Node IDs are plain strings; the valid set is determined by CHAIN_SPECS.
export type NodeId = string;

// ── Layout constants ──────────────────────────────────────────────────────────
const NODE_W = 124;
const NODE_H = 66;
const NODE_GAP = 40;       // px gap between consecutive top-row nodes
const ROW_TOP_Y = 60;      // y of top-row nodes
const ROW_BOT_Y = 250;     // y of bottom-row nodes (tier3, cloud)
const VIEW_H = 400;        // SVG height (CSS scales width to container)
const VIEW_MARGIN_R = 76;  // right-margin beyond the last node

// ── Topology spec ─────────────────────────────────────────────────────────────

/** One chain step. `task` is the Celery task name for Flower-based filtering;
 * null for synthetic nodes (tier3, cloud) that always appear. Order in
 * CHAIN_SPECS is pipeline order -- positions are auto-calculated from it. */
export interface NodeSpec {
  readonly task: string | null;
  readonly id: string;
  readonly label: string;
  readonly queue: string;
  readonly tier: Tier | "tier3";
}

/** Ordered pipeline spec -- the SINGLE source of truth for layout, path
 * generation, and live-ring matching. Keep in pipeline order. */
export const CHAIN_SPECS: readonly NodeSpec[] = [
  { task: "mesh.balanced._route",      id: "route",       label: "route",         queue: "npu",    tier: "npu"    },
  { task: "mesh.balanced._draft",       id: "draft",       label: "draft",         queue: "npu",    tier: "npu"    },
  { task: "mesh.balanced._verify",      id: "verify",      label: "verify",        queue: "verify", tier: "verify" },
  { task: "mesh.balanced._resolve_npu", id: "resolve_npu", label: "resolve_npu",   queue: "verify", tier: "verify" },
  { task: "mesh.balanced._gpu_solve",   id: "gpu_solve",   label: "gpu_solve",     queue: "gpu",    tier: "gpu"    },
  // Synthetic -- always shown regardless of what the worker has registered.
  { task: null, id: "tier3", label: "Tier 3 · CLI", queue: "—",     tier: "tier3" },
  { task: null, id: "cloud",  label: "cloud",        queue: "cloud", tier: "cloud" },
];

// ── Shared interfaces ─────────────────────────────────────────────────────────

interface ChainNode {
  readonly id: string;
  readonly label: string;
  readonly queue: string;
  readonly tier: Tier | "tier3";
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

interface PathDef {
  readonly id: string;
  readonly d: string;
  readonly kind: "flow" | "repair" | "cap";
}

interface BuiltTopology {
  readonly nodes: readonly ChainNode[];
  readonly paths: readonly PathDef[];
  readonly viewW: number;
  /** Node ids that pool .rec particles, in pipeline order (tier3 excluded). */
  readonly particleNodes: readonly string[];
  /** tier → primary particle-pool node id. npu maps to the last npu node (draft). */
  readonly tierToNodeId: ReadonlyMap<string, string>;
  /** Entering arc start per node id; null means the node never pools particles. */
  readonly enteringArcStarts: ReadonlyMap<string, { x: number; y: number } | null>;
}

// ── buildTopology ─────────────────────────────────────────────────────────────

function buildTopology(specs: readonly NodeSpec[]): BuiltTopology {
  const TOP_TIERS = new Set<string>(["npu", "verify", "gpu"]);
  const topSpecs   = specs.filter(s => TOP_TIERS.has(s.tier));
  const tier3Spec  = specs.find(s => s.tier === "tier3");
  const cloudSpec  = specs.find(s => s.tier === "cloud");

  const topNodes: ChainNode[] = topSpecs.map((s, i) => ({
    id: s.id, label: s.label, queue: s.queue, tier: s.tier,
    x: 24 + i * (NODE_W + NODE_GAP), y: ROW_TOP_Y, w: NODE_W, h: NODE_H,
  }));

  const lastVerify = [...topNodes].reverse().find(n => n.tier === "verify") ?? null;
  const lastGpu    = [...topNodes].reverse().find(n => n.tier === "gpu")    ?? null;

  const bottomNodes: ChainNode[] = [];
  if (tier3Spec && lastVerify) {
    bottomNodes.push({
      id: tier3Spec.id, label: tier3Spec.label, queue: tier3Spec.queue, tier: "tier3",
      x: lastVerify.x, y: ROW_BOT_Y, w: NODE_W, h: NODE_H,
    });
  }
  if (cloudSpec && lastGpu) {
    bottomNodes.push({
      id: cloudSpec.id, label: cloudSpec.label, queue: cloudSpec.queue, tier: "cloud",
      x: lastGpu.x, y: ROW_BOT_Y, w: NODE_W, h: NODE_H,
    });
  }

  const nodes: readonly ChainNode[] = [...topNodes, ...bottomNodes];
  const cx  = (n: ChainNode) => n.x + n.w / 2;
  const cy  = (n: ChainNode) => n.y + n.h / 2;

  const viewW = topNodes.length > 0
    ? 24 + (topNodes.length - 1) * (NODE_W + NODE_GAP) + NODE_W + VIEW_MARGIN_R
    : 400;

  // ── paths ──────────────────────────────────────────────────────────────────
  const paths: PathDef[] = [];
  if (topNodes.length > 0) {
    const f = topNodes[0]!;
    paths.push({ id: "entry-route", d: `M 0 ${cy(f)} L ${f.x} ${cy(f)}`, kind: "flow" });
  }
  for (let i = 0; i < topNodes.length - 1; i++) {
    const a = topNodes[i]!, b = topNodes[i + 1]!;
    paths.push({ id: `${a.id}-${b.id}`, d: `M ${a.x + a.w} ${cy(a)} L ${b.x} ${cy(a)}`, kind: "flow" });
  }
  const firstVerify = topNodes.find(n => n.tier === "verify") ?? null;
  if (firstVerify && lastGpu) {
    const sx = cx(lastGpu), ex = cx(firstVerify), y0 = ROW_TOP_Y, yP = ROW_TOP_Y - 34;
    paths.push({ id: "repair-loop", d: `M ${sx} ${y0} C ${sx} ${yP}, ${ex} ${yP}, ${ex} ${y0}`, kind: "repair" });
  }
  const tier3Node = bottomNodes.find(n => n.tier === "tier3") ?? null;
  if (lastVerify && tier3Node) {
    const x = cx(lastVerify);
    paths.push({ id: "cap-tier3", d: `M ${x} ${lastVerify.y + lastVerify.h} L ${x} ${tier3Node.y}`, kind: "cap" });
  }
  const cloudNode = bottomNodes.find(n => n.tier === "cloud") ?? null;
  if (tier3Node && cloudNode) {
    paths.push({ id: "tier3-cloud", d: `M ${tier3Node.x + tier3Node.w} ${cy(tier3Node)} L ${cloudNode.x} ${cy(tier3Node)}`, kind: "cap" });
  }

  // ── derived maps ────────────────────────────────────────────────────────────
  const particleNodes: string[] = [
    ...topNodes.map(n => n.id),
    ...(cloudNode ? [cloudNode.id] : []),
  ];

  // tier -> primary node. For npu, the LAST npu node (draft) is the default pool;
  // "route" records are routed to the first npu node (route) in nodeForParticle.
  const tierToNodeId = new Map<string, string>();
  for (const n of nodes) {
    if (!tierToNodeId.has(n.tier)) tierToNodeId.set(n.tier, n.id);
  }
  const npuNodes = topNodes.filter(n => n.tier === "npu");
  if (npuNodes.length > 0) tierToNodeId.set("npu", npuNodes[npuNodes.length - 1]!.id);

  const enteringArcStarts = new Map<string, { x: number; y: number } | null>();
  for (let i = 0; i < topNodes.length; i++) {
    if (i === 0) {
      enteringArcStarts.set(topNodes[0]!.id, { x: 0, y: cy(topNodes[0]!) });
    } else {
      const prev = topNodes[i - 1]!;
      enteringArcStarts.set(topNodes[i]!.id, { x: prev.x + prev.w, y: cy(prev) });
    }
  }
  if (cloudNode && tier3Node) {
    enteringArcStarts.set(cloudNode.id, { x: tier3Node.x + tier3Node.w, y: cy(tier3Node) });
  }
  if (tier3Node) enteringArcStarts.set(tier3Node.id, null);

  return { nodes, paths, viewW, particleNodes, tierToNodeId, enteringArcStarts };
}

// ── Module-level topology state ───────────────────────────────────────────────

let _topo: BuiltTopology = buildTopology(CHAIN_SPECS);

/** Call at server startup to filter the displayed topology to tasks actually
 * registered on the current Celery worker (query Flower `/api/workers`).
 * Synthetic nodes (tier3, cloud) are always included. Falls back to the full
 * CHAIN_SPECS when called with no argument or an empty set. */
export function setTopology(registeredTasks?: ReadonlySet<string>): void {
  const specs = (registeredTasks && registeredTasks.size > 0)
    ? CHAIN_SPECS.filter(s => s.task === null || registeredTasks.has(s.task))
    : CHAIN_SPECS;
  _topo = buildTopology(specs);
}

// ── Convenience helpers (use _topo) ──────────────────────────────────────────

function nodeById(id: string): ChainNode {
  const n = _topo.nodes.find(n => n.id === id);
  if (!n) throw new Error(`unknown node id: ${id}`);
  return n;
}

/** Which chain node a .rec particle belongs to, from its tier + tool. The npu
 * lane carries both route and draft; a tool=route record lands on the route
 * node, everything else lands on the primary npu node (draft). Exported for
 * unit tests. */
export function nodeForParticle(p: Particle): string {
  if (p.tier === "npu" && p.tool === "route") {
    const routeNode = _topo.nodes.find(n => n.tier === "npu" && n.id === "route");
    if (routeNode) return routeNode.id;
  }
  return _topo.tierToNodeId.get(p.tier) ?? p.tier;
}

const PARTICLES_PER_NODE = 12;

/** SD-P1: how long a freshly-ingested particle takes to ride its entering arc.
 * Exported so unit tests can pin the boundary case `nowMs - tsMs === ANIM_MS`. */
export const ANIM_MS = 1500;

/** For each particle node, the "entering arc" start point -- the off-node
 * position particles glide FROM during the in-flight phase. Exported for unit
 * tests. */
export function enteringArcStart(id: NodeId): { x: number; y: number } | null {
  if (!_topo.enteringArcStarts.has(id)) return null;
  return _topo.enteringArcStarts.get(id) ?? null;
}

/** Pure render geometry for a particle. Exported so tests can assert position
 * math without booting a DashContext. */
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
  if (ageMs >= ANIM_MS || ageMs < 0) return { cx: poolCx, cy: poolCy, inFlight: false };

  const start = enteringArcStart(id);
  if (!start) return { cx: poolCx, cy: poolCy, inFlight: false };

  const progress = ageMs / ANIM_MS;
  return {
    cx: start.x + (poolCx - start.x) * progress,
    cy: start.y + (poolCy - start.y) * progress,
    inFlight: true,
  };
}

/** How long after ingest a node stays "hot" (bright ring + glow). Exported for
 * unit tests pinning the boundary. */
export const HOT_MS = 2600;

/** Is this node currently hot? tier3 has no lane; it goes hot from a
 * `capped->tier3` Outcome instead. Pure -- no DOM. Exported. */
export function isNodeHot(
  id: NodeId,
  byNode: Map<NodeId, Particle[]>,
  lastOutcome: LastOutcome | null,
  nowMs: number,
): boolean {
  if (id === "tier3") {
    if (lastOutcome === null || lastOutcome.finalTier !== "capped->tier3") return false;
    const age = nowMs - lastOutcome.tsMs;
    return age >= 0 ? age < HOT_MS : true;
  }
  const arr = byNode.get(id);
  if (!arr || arr.length === 0) return false;
  const newest = arr[arr.length - 1]!;
  const age = nowMs - newest.tsMs;
  if (age < 0) return true;
  return age < HOT_MS;
}

/** Is this node executing right now, per the LIVE lane? Matched by id OR label
 * so a live node id can match either field; live-only ids with no flow node
 * (merge_gpu/done/pick) match nothing and are ignored. Pure -- no DOM.
 * Exported for unit tests. */
export function isNodeActive(
  id: string,
  label: string,
  activeNodes: ReadonlySet<string>,
): boolean {
  return activeNodes.has(id) || activeNodes.has(label);
}

export const FLASH_MS = 1600;

/** True while the most-recent Outcome's flash window is still open. Pure. */
export function isFlashing(lastOutcome: LastOutcome | null, nowMs: number): boolean {
  if (lastOutcome === null) return false;
  const age = nowMs - lastOutcome.tsMs;
  return age >= 0 && age < FLASH_MS;
}

/** SD-P3 heartbeat rate. Exported for app.ts and tests. */
export const HEARTBEAT_MS = 80;

/** Does the current store state warrant another TICK before any record arrives?
 * Pure -- app.ts composes it into a setTimeout chain. */
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
  if (lastOutcome !== null && lastOutcome.finalTier === "capped->tier3") {
    const age = nowMs - lastOutcome.tsMs;
    if (age >= 0 && age < HOT_MS) return true;
  }
  return isFlashing(lastOutcome, nowMs);
}

// ── Static topology SVG ───────────────────────────────────────────────────────

/** The static topology SVG -- rendered into the initial HTTP paint. Call
 * setTopology() before first render to filter to registered tasks. */
export function cascadeFlowTopology(): VNode {
  const { nodes, paths, viewW } = _topo;
  return h(
    "svg",
    {
      class: "topology",
      viewBox: `0 0 ${String(viewW)} ${String(VIEW_H)}`,
      xmlns: "http://www.w3.org/2000/svg",
      "aria-label": "edge-cascade Canvas chain topology",
    },
    h("g", { class: "paths" },
      ...paths.map(p =>
        h("path", { id: p.id, class: `arc arc--${p.kind}`, d: p.d, fill: "none" }),
      ),
    ),
    h("g", { class: "nodes" },
      ...nodes.map(n =>
        h("g", { class: `node node--${n.id} node--tone-${n.tier}` },
          h("rect", { class: "node-rect", x: String(n.x), y: String(n.y), width: String(n.w), height: String(n.h), rx: "7", ry: "7" }),
          h("text", { class: "node-label", x: String(n.x + n.w / 2), y: String(n.y + n.h / 2 + 1), "text-anchor": "middle" }, n.label),
          h("text", { class: "node-queue", x: String(n.x + n.w / 2), y: String(n.y + n.h - 10), "text-anchor": "middle" },
            n.queue === "—" ? "tier 3" : `Q:${n.queue}`),
        ),
      ),
    ),
  );
}

// ── Live overlays ─────────────────────────────────────────────────────────────

/** The dynamic overlay -- one live region; re-renders on every TICK emit. */
export const cascadeFlowRegion: LiveRegion<DashContext> = liveRegion(
  "cascade-flow",
  (ctx) => overlaySvg(ctx),
);

/** Emitted ONLY by the live source on a node-state delta, separate from the
 * ledger TICK. The spin region subscribes to this alone. */
export const LIVE = "live";

/** The spinning-ring overlay as its OWN live region (the liveness lane),
 * decoupled from the ledger-driven cascadeFlowRegion. */
export const cascadeSpinRegion: LiveRegion<DashContext> = liveRegion(
  "cascade-spin",
  (ctx) => spinOverlaySvg(ctx),
);

function spinOverlaySvg(ctx: DashContext): VNode {
  const activeNodes = ctx.store.activeNodes();
  const { nodes, viewW } = _topo;
  return h("svg", { class: "overlay", viewBox: `0 0 ${String(viewW)} ${String(VIEW_H)}`, xmlns: "http://www.w3.org/2000/svg", "aria-hidden": "true" },
    h("g", { class: "node-spins" }, ...nodes.map(n => spinRing(n, activeNodes))),
  );
}

function overlaySvg(ctx: DashContext): VNode {
  const nowMs = ctx.nowMs();
  const particles = ctx.store.particles();
  const byNode = bucketByNode(particles);
  const lastOutcome = ctx.store.lastOutcome();
  const { nodes, particleNodes, viewW } = _topo;
  return h("svg", { class: "overlay", viewBox: `0 0 ${String(viewW)} ${String(VIEW_H)}`, xmlns: "http://www.w3.org/2000/svg", "aria-hidden": "true" },
    outcomeFlash(lastOutcome, nowMs, viewW),
    h("g", { class: "node-hots" }, ...nodes.map(n => hotRing(n, byNode, lastOutcome, nowMs))),
    h("g", { class: "node-stats" }, ...particleNodes.map(id => nodeStat(byNode.get(id) ?? [], id))),
    h("g", { class: "particles" }, ...particles.flatMap(p => particleCircle(p, indexInNode(p, byNode), nowMs))),
    outcomeBanner(lastOutcome, nowMs, viewW),
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

function particleCircle(p: Particle, idx: number, nowMs: number): VNode[] {
  if (idx >= PARTICLES_PER_NODE) return [];
  const pos = particlePosition(p, idx, nowMs);
  const inFlightCls = pos.inFlight ? " particle--in-flight" : "";
  const failCls = p.ok ? "" : " particle--fail";
  return [
    h("circle", { id: p.id, class: `particle particle--${p.tier}${failCls}${inFlightCls}`, cx: String(pos.cx), cy: String(pos.cy), r: "4" }),
  ];
}

function hotRing(n: ChainNode, byNode: Map<NodeId, Particle[]>, lastOutcome: LastOutcome | null, nowMs: number): VNode {
  const hot = isNodeHot(n.id, byNode, lastOutcome, nowMs);
  const cls = hot ? `node-hot node-hot--hot node-hot--tone-${n.tier}` : `node-hot node-hot--tone-${n.tier}`;
  return h("rect", { class: cls, x: String(n.x - 3), y: String(n.y - 3), width: String(n.w + 6), height: String(n.h + 6), rx: "9", ry: "9", fill: "none", "aria-hidden": "true" });
}

function spinRing(n: ChainNode, activeNodes: ReadonlySet<string>): VNode {
  const spinning = isNodeActive(n.id, n.label, activeNodes);
  const cls = spinning ? `node-spin node-spin--spinning node-spin--tone-${n.tier}` : `node-spin node-spin--tone-${n.tier}`;
  return h("rect", { class: cls, x: String(n.x - 6), y: String(n.y - 6), width: String(n.w + 12), height: String(n.h + 12), rx: "12", ry: "12", fill: "none", "aria-hidden": "true" });
}

function nodeStat(inNode: readonly Particle[], id: NodeId): VNode {
  const n = nodeById(id);
  return h("text", { class: `node-stat node-stat--${n.tier}`, x: String(n.x + n.w - 8), y: String(n.y + 16), "text-anchor": "end" }, String(inNode.length));
}

function outcomeFlash(lastOutcome: LastOutcome | null, nowMs: number, viewW: number): VNode {
  if (!isFlashing(lastOutcome, nowMs)) return h("g", { class: "outcome-flash" });
  const tone = lastOutcome!.won ? "win" : "lose";
  return h("g", { class: `outcome-flash outcome-flash--active outcome-flash--${tone}` },
    h("rect", { class: "outcome-flash-rect", x: "0", y: "0", width: String(viewW), height: String(VIEW_H), "aria-hidden": "true" }),
  );
}

function outcomeBanner(lastOutcome: LastOutcome | null, nowMs: number, viewW: number): VNode {
  if (!isFlashing(lastOutcome, nowMs)) return h("g", { class: "outcome-banner" });
  const o = lastOutcome!;
  const tone = o.won ? "win" : "lose";
  const word = o.won ? "LOCAL WIN" : "CAPPED";
  const sub  = o.won ? `resolved @ ${o.finalTier}` : "→ Tier 3 takeover";
  return h("g", { class: `outcome-banner outcome-banner--active outcome-banner--${tone}` },
    h("text", { class: "outcome-banner-word",  x: String(viewW / 2), y: String(VIEW_H / 2 - 6),  "text-anchor": "middle" }, word),
    h("text", { class: "outcome-banner-sub",   x: String(viewW / 2), y: String(VIEW_H / 2 + 20), "text-anchor": "middle" }, sub),
  );
}
