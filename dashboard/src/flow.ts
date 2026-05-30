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

/** Fallback spec used when no Beat-pushed graph has arrived yet.
 * The REAL topology is pushed from Python via `cascade.push_topology` (Beat
 * task + worker_ready signal) and rendered by `setTopologyGraph()`. This
 * spec only affects the initial paint before the first Beat message arrives. */
export const CHAIN_SPECS: readonly NodeSpec[] = [
  { task: "mesh.balanced._route",       id: "route",             label: "route",             queue: "npu",    tier: "npu"    },
  { task: "mesh.balanced._draft",        id: "draft",             label: "draft",             queue: "npu",    tier: "npu"    },
  { task: "mesh.balanced._verify",       id: "verify_syntax",     label: "verify_syntax",     queue: "verify", tier: "verify" },
  { task: null,                           id: "verify_functional", label: "verify_func",       queue: "verify", tier: "verify" },
  { task: "mesh.balanced._resolve_npu",  id: "resolve_npu",       label: "resolve_npu",       queue: "verify", tier: "verify" },
  { task: "mesh.balanced._gpu_solve",    id: "gpu_solve",         label: "gpu_solve",         queue: "gpu",    tier: "gpu"    },
  // repair_prompt is part of the GPU repair arc, not the forward chain.
  // It is not in the top-row sequence here; the Beat-pushed graph positions it
  // correctly above the repair arc. Particles route to gpu_solve as fallback.
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

  // Synthetic nodes always appear. Anchor to their natural position (lastVerify /
  // lastGpu) when present; fall back to the rightmost top-row node so the SVG
  // is never blank and nodeById never throws on a cloud/tier3-tier record.
  const lastTopNode = topNodes[topNodes.length - 1] ?? null;
  const tier3AnchorX = (lastVerify ?? lastTopNode)?.x ?? 24;
  const cloudAnchorX = (lastGpu    ?? lastTopNode)?.x ?? 24;

  const bottomNodes: ChainNode[] = [];
  if (tier3Spec) {
    bottomNodes.push({
      id: tier3Spec.id, label: tier3Spec.label, queue: tier3Spec.queue, tier: "tier3",
      x: tier3AnchorX, y: ROW_BOT_Y, w: NODE_W, h: NODE_H,
    });
  }
  if (cloudSpec) {
    bottomNodes.push({
      id: cloudSpec.id, label: cloudSpec.label, queue: cloudSpec.queue, tier: "cloud",
      x: cloudAnchorX, y: ROW_BOT_Y, w: NODE_W, h: NODE_H,
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
  // Cap arc: GPU repair exhausted → Tier 3. Originates at gpu_solve when
  // present; falls back to the last top-row node so the arc is never missing.
  const capSource = lastGpu ?? lastTopNode;
  if (capSource && tier3Node) {
    const x0 = cx(capSource), x1 = cx(tier3Node);
    paths.push({ id: "cap-tier3", d: `M ${x0} ${capSource.y + capSource.h} L ${x1} ${tier3Node.y}`, kind: "cap" });
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
  // Override: for tiers with multiple nodes, pool particles at the LAST node
  // so the rightmost step of each tier lights up. NPU: draft (not route).
  // Verify: resolve_npu (not verify). Without this override, first-encounter wins.
  const npuNodes    = topNodes.filter(n => n.tier === "npu");
  const verifyNodes = topNodes.filter(n => n.tier === "verify");
  if (npuNodes.length    > 0) tierToNodeId.set("npu",    npuNodes[npuNodes.length - 1]!.id);
  if (verifyNodes.length > 0) tierToNodeId.set("verify", verifyNodes[verifyNodes.length - 1]!.id);

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

// ── TOPOLOGY signal + live topology region ────────────────────────────────────

/** Signal emitted when the Beat task pushes a new topology graph. The topology
 * SVG region subscribes to this — topology changes push to all connected
 * browser clients without a page refresh. */
export const TOPOLOGY = "topology";

/** Raw graph payload shape published by cascade.push_topology (Beat task).
 * Mirrors cascade/topology_graph.py TopologyGraph.to_dict(). */
interface RawGraphPayload {
  name?: string;
  nodes: { id: string; label: string; tier: string; queue: string; task: string | null }[];
  edges: { from: string; to: string; kind: string }[];
}

/**
 * Build a BuiltTopology from a raw graph payload (nodes + directed edges).
 * Uses hierarchical layout: assigns ranks via topological sort on flow/alt
 * edges, positions parallel alternatives at different rows, floats
 * repair_prompt above the repair arc, places tier3/cloud in the bottom row.
 */
function buildTopologyFromGraph(g: RawGraphPayload): BuiltTopology {
  const TOP_TIERS = new Set<string>(["npu", "verify", "gpu"]);
  const REPAIR_KINDS = new Set(["repair"]);
  const SKIP_FOR_RANK = new Set(["repair", "cap", "parallel"]);

  // Step 1: rank assignment via longest-path on flow/alt edges only.
  const rankOf = new Map<string, number>();
  const pred = new Map<string, string[]>(); // predecessors
  for (const n of g.nodes) { pred.set(n.id, []); rankOf.set(n.id, 0); }
  for (const e of g.edges) {
    if (!SKIP_FOR_RANK.has(e.kind)) pred.get(e.to)?.push(e.from);
  }
  // BFS-style longest-path: process in topological order
  const processed = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const n of g.nodes) {
      if (processed.has(n.id)) continue;
      const preds = pred.get(n.id) ?? [];
      if (preds.every(p => processed.has(p))) {
        const newRank = preds.length === 0 ? 0 : Math.max(...preds.map(p => rankOf.get(p) ?? 0)) + 1;
        if (newRank !== rankOf.get(n.id)) { rankOf.set(n.id, newRank); changed = true; }
        processed.add(n.id);
      }
    }
  }

  // Step 2: separate nodes by layout role.
  const repairNodeIds = new Set(
    g.edges.filter(e => REPAIR_KINDS.has(e.kind)).flatMap(e => [e.from, e.to])
  );
  // A node is "repair-only" if it appears only in repair edges and not in the main flow.
  const mainEdgeNodes = new Set(
    g.edges.filter(e => !SKIP_FOR_RANK.has(e.kind)).flatMap(e => [e.from, e.to])
  );
  const isRepairFloating = (id: string) => repairNodeIds.has(id) && !mainEdgeNodes.has(id);

  const mainNodes  = g.nodes.filter(n => TOP_TIERS.has(n.tier) && !isRepairFloating(n.id));
  const repairNodes = g.nodes.filter(n => isRepairFloating(n.id));
  const capNodes   = g.nodes.filter(n => n.tier === "tier3" || n.tier === "cloud");

  // Step 3: group main nodes by rank, assign row within rank.
  const byRank = new Map<number, typeof mainNodes>();
  for (const n of mainNodes) {
    const r = rankOf.get(n.id) ?? 0;
    if (!byRank.has(r)) byRank.set(r, []);
    byRank.get(r)!.push(n);
  }
  const posOf = new Map<string, { x: number; y: number }>();
  const maxRank = mainNodes.length > 0 ? Math.max(...mainNodes.map(n => rankOf.get(n.id) ?? 0)) : 0;

  // Vertical centering offset for multi-row ranks
  const ROW_INNER_GAP = 24;
  for (const [rank, nodesAtRank] of byRank) {
    const count = nodesAtRank.length;
    const totalH = count * NODE_H + (count - 1) * ROW_INNER_GAP;
    const startY = ROW_TOP_Y - Math.floor((totalH - NODE_H) / 2);
    nodesAtRank.forEach((n, rowIdx) => {
      posOf.set(n.id, {
        x: 24 + rank * (NODE_W + NODE_GAP),
        y: startY + rowIdx * (NODE_H + ROW_INNER_GAP),
      });
    });
  }

  // Step 4: position repair-floating nodes above the repair arc midpoint.
  const repairEdges = g.edges.filter(e => e.kind === "repair");
  for (const rn of repairNodes) {
    // Find the GPU-side source and the verify-side target of the repair arc.
    const inEdge  = repairEdges.find(e => e.to === rn.id);
    const outEdge = repairEdges.find(e => e.from === rn.id);
    const srcPos  = inEdge  ? posOf.get(inEdge.from)  : null;
    const dstPos  = outEdge ? posOf.get(outEdge.to)   : null;
    const midX = srcPos && dstPos ? (srcPos.x + dstPos.x) / 2 : 24 + maxRank * (NODE_W + NODE_GAP) / 2;
    posOf.set(rn.id, { x: midX - NODE_W / 2, y: ROW_TOP_Y - 90 });
  }

  // Step 5: position cap nodes below their sources.
  const capEdgesByTo = new Map<string, string>(); // to → from
  for (const e of g.edges.filter(e => e.kind === "cap")) capEdgesByTo.set(e.to, e.from);
  for (const cn of capNodes) {
    const srcId = capEdgesByTo.get(cn.id);
    const srcPos = srcId ? posOf.get(srcId) : null;
    posOf.set(cn.id, { x: srcPos?.x ?? (24 + maxRank * (NODE_W + NODE_GAP)), y: ROW_BOT_Y });
  }

  // Build ChainNode objects.
  const allRawNodes = [...mainNodes, ...repairNodes, ...capNodes];
  const nodes: ChainNode[] = allRawNodes
    .filter(n => posOf.has(n.id))
    .map(n => {
      const p = posOf.get(n.id)!;
      return { id: n.id, label: n.label, queue: n.queue, tier: n.tier as Tier | "tier3", x: p.x, y: p.y, w: NODE_W, h: NODE_H };
    });

  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const cx = (n: ChainNode) => n.x + n.w / 2;
  const cy = (n: ChainNode) => n.y + n.h / 2;

  // Step 6: compute viewW and viewH.
  const xs = nodes.map(n => n.x + n.w);
  const viewW = xs.length > 0 ? Math.max(...xs) + VIEW_MARGIN_R : 400;

  // Step 7: build paths.
  const paths: PathDef[] = [];
  // Entry arc to first main node (leftmost, lowest rank)
  const firstMain = [...byRank.get(0) ?? []].map(n => nodeMap.get(n.id)).filter(Boolean)[0];
  if (firstMain) {
    paths.push({ id: "entry-route", d: `M 0 ${cy(firstMain)} L ${firstMain.x} ${cy(firstMain)}`, kind: "flow" });
  }

  for (const e of g.edges) {
    const from = nodeMap.get(e.from);
    const to   = nodeMap.get(e.to);
    if (!from || !to) continue;
    const kind = (e.kind === "flow" || e.kind === "alt") ? "flow"
               : (e.kind === "repair") ? "repair"
               : "cap";

    if (e.kind === "repair") {
      // Curved arc through the floating repair node
      const y0 = from.y, yP = from.y - 34;
      const sx = cx(from), ex = cx(to);
      if (isRepairFloating(from.id) || isRepairFloating(to.id)) {
        // Straight line to/from the floating repair node
        paths.push({ id: `${e.from}-${e.to}`, d: `M ${cx(from)} ${cy(from)} L ${cx(to)} ${cy(to)}`, kind: "repair" });
      } else {
        // Pure arc (no floating node) — legacy fallback
        paths.push({ id: `repair-loop`, d: `M ${sx} ${y0} C ${sx} ${yP}, ${ex} ${yP}, ${ex} ${y0}`, kind: "repair" });
      }
    } else {
      const fy = cy(from);
      if (from.y === to.y) {
        paths.push({ id: `${e.from}-${e.to}`, d: `M ${from.x + from.w} ${fy} L ${to.x} ${fy}`, kind });
      } else {
        paths.push({ id: `${e.from}-${e.to}`, d: `M ${from.x + from.w} ${fy} L ${to.x} ${cy(to)}`, kind });
      }
    }
  }

  // Step 8: particle nodes, tierToNodeId, enteringArcStarts.
  const particleNodes: string[] = nodes
    .filter(n => n.tier !== "tier3")
    .map(n => n.id);

  const tierToNodeId = new Map<string, string>();
  for (const n of nodes) {
    if (!tierToNodeId.has(n.tier)) tierToNodeId.set(n.tier, n.id);
  }
  const verifyNodes = nodes.filter(n => n.tier === "verify" && !isRepairFloating(n.id));
  const npuNodes    = nodes.filter(n => n.tier === "npu");
  if (npuNodes.length    > 0) tierToNodeId.set("npu",    npuNodes[npuNodes.length - 1]!.id);
  if (verifyNodes.length > 0) tierToNodeId.set("verify", verifyNodes[verifyNodes.length - 1]!.id);

  const enteringArcStarts = new Map<string, { x: number; y: number } | null>();
  for (const n of nodes) {
    if (n.tier === "tier3") { enteringArcStarts.set(n.id, null); continue; }
    const incomingEdge = g.edges.find(e => e.to === n.id && (e.kind === "flow" || e.kind === "alt" || e.kind === "parallel"));
    if (!incomingEdge) {
      enteringArcStarts.set(n.id, { x: 0, y: cy(n) }); // entry node
    } else {
      const fromNode = nodeMap.get(incomingEdge.from);
      if (fromNode) enteringArcStarts.set(n.id, { x: fromNode.x + fromNode.w, y: cy(fromNode) });
      else enteringArcStarts.set(n.id, { x: 0, y: cy(n) });
    }
  }

  return { nodes, paths, viewW, particleNodes, tierToNodeId, enteringArcStarts };
}

/** Called by liveSource.onTopologyChange when the Beat task pushes a new
 * graph. Replaces _topo with a layout built from the real Python graph spec.
 * The topology SVG live region re-renders automatically when TOPOLOGY is emitted. */
export function setTopologyGraph(payload: unknown): void {
  try {
    const g = payload as RawGraphPayload;
    if (!Array.isArray(g.nodes) || !Array.isArray(g.edges)) return;
    _topo = buildTopologyFromGraph(g);
  } catch {
    // Malformed payload — keep current topology.
  }
}

/** The topology SVG as a Vinyl live region -- re-renders when TOPOLOGY is
 * emitted (Beat push) so connected browsers see the updated graph without
 * a page refresh. */
export const cascadeFlowTopologyRegion: LiveRegion<DashContext> = liveRegion(
  "cascade-topology",
  (_ctx) => cascadeFlowTopology(),
);

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
  // NPU: differentiate route from draft by tool name.
  if (p.tier === "npu" && p.tool === "route") {
    const routeNode = _topo.nodes.find(n => n.tier === "npu" && n.id === "route");
    if (routeNode) return routeNode.id;
  }
  // Verify: route to the specific operation node by tool name when present.
  // repair_prompt falls back to gpu_solve when no repair_prompt node exists
  // (CHAIN_SPECS fallback mode — the Beat-pushed graph has the real node).
  if (p.tier === "verify") {
    const opNode = _topo.nodes.find(n => n.id === p.tool);
    if (opNode) return opNode.id;
    if (p.tool === "repair_prompt") {
      const gpuNode = _topo.nodes.find(n => n.id === "gpu_solve");
      if (gpuNode) return gpuNode.id;
    }
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
