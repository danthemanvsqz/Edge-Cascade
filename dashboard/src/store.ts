/**
 * In-memory derived state for the dashboard. All facts the UI shows come from
 * here; the live regions read these views and the tailer's `onRecord` calls
 * `ingest()`.
 *
 * Mirrors `dashboard.py::compute_metrics` for the parts the river needs:
 *   - per-tier 1-second bucketed counts (the sparklines / particles)
 *   - the load-bearing `cloud_calls` + `usd` invariant (spend.clean = 0 / $0)
 *   - the most recent record (the "now playing" panel)
 *
 * Designed so the SignalHub callers in `server.ts` don't have to think about
 * time/buffer geometry: `ingest()` is the only mutator, the rest are pure
 * snapshots. The store itself decides what is a "particle" (a record from a
 * known tier with parseable headers); experiment lanes and unknown servers
 * are accepted-and-ignored so a future tier addition is one line.
 */

export type Tier = "npu" | "gpu" | "verify" | "cloud";

/** Tiers that actually produce drafted output (and therefore degeneration
 * observations). The verify tier is a gate, the cloud tier is escalation;
 * neither runs through `cascade.mesh.solve`'s PD-1 observer. */
export type DegenTier = "npu" | "gpu" | "igpu";

/** One PD-1 v1 observation as the dashboard sees it. Mirrors the field shape
 * written by `cascade.degen_recorder.make_degen_recorder`. Field types match
 * the parsed `.rec` projection (strings → numbers/bools at ingest time so the
 * panel doesn't reparse on every render). */
export interface DegenObservation {
  /** Wall-clock ms (derived from the record's `ts`, same fallback rules as
   * `Particle.tsMs`). */
  readonly tsMs: number;
  readonly tier: DegenTier;
  /** Blended quality signal in [0, 1] — 0 clean, 1 every metric tripped. */
  readonly score: number;
  /** Trip flag — true iff any text metric or tier-availability check fires. */
  readonly degraded: boolean;
  /** Human-legible reason tags from the detector. May be empty when
   * `degraded` is false. */
  readonly reasons: readonly string[];
}

/** A particle is the dashboard's view of a single `.rec` record: just the
 * fields the cascade-flow SVG renders + enough provenance to derive a stable
 * DOM id. */
export interface Particle {
  /** Stable DOM id: `p-<server>-<seq>`. Used by the OOB swap. */
  readonly id: string;
  readonly tier: Tier;
  readonly server: string;
  readonly seq: string;
  readonly tool: string;
  /** Unix milliseconds — derived from the record's `ts` field; falls back
   * to `Date.now()` if the field is absent or unparseable. */
  readonly tsMs: number;
  /** Latency in milliseconds, 0 if missing. */
  readonly latencyMs: number;
  /** True unless the record explicitly carries `ok=false`. */
  readonly ok: boolean;
}

/** Spend snapshot. `clean` is the load-bearing invariant: the cascade is
 * meant to stay local-only, so any `cloud_calls > 0` or `usd > 0` should
 * render the spend panel red. */
export interface Spend {
  readonly cloudCalls: number;
  readonly usd: number;
  readonly clean: boolean;
}

/** Per-tier health snapshot, derived from the most-recent `tool=status`
 * record from that tier. `available` defaults `true` (no signal = no
 * degradation; a tier we have never polled is not "down", it is just
 * unknown). A `status` record whose `result.available` is missing,
 * non-boolean, or unparseable does NOT flip the flag -- only an explicit
 * `false` does. */
export interface TierHealth {
  readonly available: boolean;
  /** Wall-clock ms of the most-recent status record from this tier, or
   * null if we have not yet seen one this session. */
  readonly lastSeenMs: number | null;
}

/** Cascade health snapshot. `degraded` flips iff any tier's most-recent
 * status record explicitly carried `available:false`. The visibility
 * deficiency this closes: Phase A was built with NPU `available:false`
 * the entire time, and the dashboard had no surface for it. */
export interface Health {
  readonly tiers: Readonly<Record<Tier, TierHealth>>;
  readonly degraded: boolean;
}

/** Mesh effectiveness snapshot (SD-4). Cumulative counts over every
 * `cascade.rec` record seen this session.
 *  - `resolvedNpu` / `resolvedIgpu` / `resolvedGpu` — the cascade returned
 *    an answer at that tier (final_tier = "npu" | "igpu" | "gpu"). iGPU is
 *    the optional Tier-1b drafter (a larger 3B model on the Intel iGPU);
 *    when wired it can win the cascade alongside the NPU. Counted
 *    separately rather than rolled into `resolvedNpu` so the UI can
 *    attribute wins to the actual node that produced them.
 *  - `capped` — the bounded repair loop exhausted; Tier 3 takeover
 *    (final_tier = "capped->tier3").
 *  - `draftSkipped` — the router pre-judged the Tier-1 draft not worth
 *    trying (trace contains "draft skipped"). Counted ON TOP of an outcome,
 *    not instead of one — a skipped-then-gpu-resolved run increments both
 *    `resolvedGpu` and `draftSkipped`.
 *  - `effectivenessPct` — `(resolvedNpu + resolvedIgpu + resolvedGpu) /
 *    total * 100`, 0 when `total === 0`. The "mesh is working X% of the
 *    time" headline. */
export interface CascadeOutcomes {
  readonly resolvedNpu: number;
  readonly resolvedIgpu: number;
  readonly resolvedGpu: number;
  readonly capped: number;
  readonly draftSkipped: number;
  readonly total: number;
  readonly effectivenessPct: number;
}

export interface Store {
  /** Ingest one record from the tailer. Returns the particle iff the record
   * mapped to a known tier (so the caller knows whether to push it through
   * the flow region). */
  ingest(server: string, record: Record<string, string>): Particle | null;
  /** The last N particles in arrival order (oldest → newest). N is bounded
   * by the queue ceiling passed to `createStore`. */
  particles(): readonly Particle[];
  /** Sparkline = the last `WINDOW_SECONDS` 1-second buckets ending at
   * `floor(nowMs / 1000)`, oldest → newest. Always exactly `WINDOW_SECONDS`
   * entries (zero-padded). */
  sparkline(tier: Tier, nowMs: number): readonly number[];
  /** Most recent particle seen (any tier), or null if none yet. Carries the
   * raw record map so the "now playing" panel can show tool args / result
   * truncations without the store knowing about presentation. */
  mostRecent(): { particle: Particle; record: Record<string, string> } | null;
  /** Spend snapshot. */
  spend(): Spend;
  /** Cascade health snapshot. */
  health(): Health;
  /** Per-tier degeneration history (oldest → newest). Bounded by
   * `degenCeiling`. Empty when no PD-1 observation has been seen yet for
   * `tier`. */
  degen(tier: DegenTier): readonly DegenObservation[];
  /** Mesh effectiveness snapshot (cumulative this session). */
  cascadeOutcomes(): CascadeOutcomes;
  /** Wall-clock ms of the most recent particle ingest for `tier`, or null
   * if none yet. Drives the SD-P2 active-node pulse on the flow SVG --
   * "this zone got a record in the last PULSE_MS." Independent of the
   * SD-P1 motion ANIM_MS so they can tune separately. */
  lastIngestMs(tier: Tier): number | null;
  /** Total particles seen across all tiers (for the rate meter). */
  totalCount(): number;
}

export interface CreateStoreOptions {
  /** Max particles retained in the queue. Default 200. */
  readonly particleCeiling?: number;
  /** Max degeneration observations retained per tier. Default 30 — sized
   * to match the SD-2b panel's 60-wide bar SVG (30 slots, 2px each: a 1px
   * bar + 1px gap). Retaining more than the panel paints would surface a
   * mismatch under future re-skins; keep the two in lockstep here. */
  readonly degenCeiling?: number;
}

export const WINDOW_SECONDS = 60;
const DEFAULT_CEILING = 200;
const DEFAULT_DEGEN_CEILING = 30;
/** The server name written by `cascade.degen_recorder` — the dashboard side
 * lane for PD-1 observations. Exported so callers wiring TICK emission can
 * recognise these records as "accepted but not a particle". */
export const DEGEN_SERVER = "cascade-degeneration";
/** The server name written by `cascade.mesh.solve` -- one record per cascade
 * Outcome (final_tier + trace). Source for the mesh-effectiveness panel
 * (SD-4): resolved vs capped vs draft-skipped over the session. Sidelane,
 * not a particle producer (same shape as DEGEN_SERVER). */
export const CASCADE_SERVER = "cascade";
const DEGEN_TIERS: readonly DegenTier[] = ["npu", "gpu", "igpu"];
const CLOUD_GEN_TOOLS = new Set(["ask", "generate"]);
const STATUS_TOOL = "status";
const TIERS: readonly Tier[] = ["npu", "gpu", "verify", "cloud"];

const SERVER_TO_TIER: ReadonlyMap<string, Tier> = new Map([
  ["edge-npu", "npu"],
  ["edge-gpu", "gpu"],
  ["edge-verify", "verify"],
  ["edge-cloud", "cloud"],
]);

export function serverToTier(server: string): Tier | null {
  return SERVER_TO_TIER.get(server) ?? null;
}

export function createStore(options: CreateStoreOptions = {}): Store {
  const ceiling = options.particleCeiling ?? DEFAULT_CEILING;
  const degenCeiling = options.degenCeiling ?? DEFAULT_DEGEN_CEILING;
  const queue: Particle[] = [];
  const buckets: Record<Tier, Map<number, number>> = {
    npu: new Map(),
    gpu: new Map(),
    verify: new Map(),
    cloud: new Map(),
  };
  let recent: { particle: Particle; record: Record<string, string> } | null =
    null;
  let cloudCalls = 0;
  let usd = 0;
  let totalParticles = 0;
  const health: Record<Tier, { available: boolean; lastSeenMs: number | null }> = {
    npu: { available: true, lastSeenMs: null },
    gpu: { available: true, lastSeenMs: null },
    verify: { available: true, lastSeenMs: null },
    cloud: { available: true, lastSeenMs: null },
  };
  const degenLog: Record<DegenTier, DegenObservation[]> = {
    npu: [],
    gpu: [],
    igpu: [],
  };
  let resolvedNpu = 0;
  let resolvedIgpu = 0;
  let resolvedGpu = 0;
  let cappedRuns = 0;
  let draftSkippedRuns = 0;
  let totalRuns = 0;
  // SD-P2: per-tier most-recent particle ingest wall-clock. Updated on every
  // particle (status records ALSO update it -- "the tier is busy" is the
  // signal, not "the tier produced a particle for the flow river"). null
  // until first activity from that tier.
  const lastIngest: Record<Tier, number | null> = {
    npu: null,
    gpu: null,
    verify: null,
    cloud: null,
  };

  function ingest(
    server: string,
    record: Record<string, string>,
  ): Particle | null {
    // Spend accounting first -- counts every edge-cloud record, even ones
    // we don't render (status calls). Mirrors dashboard.py::compute_metrics.
    if (server === "edge-cloud") {
      const tool = record.tool ?? "";
      if (CLOUD_GEN_TOOLS.has(tool)) cloudCalls += 1;
      const cost = extractCostUsd(record.result);
      if (cost > 0) usd += cost;
    }

    // SD-2b: degeneration lane is a side-channel, not a particle producer.
    // The panel reads from `degen(tier)`; the cascade-flow SVG and rate
    // meter never see these records. `app.ts` watches `server` directly
    // (not the return value) to know whether to fire TICK.
    if (server === DEGEN_SERVER) {
      ingestDegen(record);
      return null;
    }

    // SD-4: cascade-outcomes lane. One record per mesh.solve Outcome; updates
    // the effectiveness counters and returns null (sidelane, not a particle).
    if (server === CASCADE_SERVER) {
      ingestCascadeOutcome(record);
      return null;
    }

    const tier = serverToTier(server);
    if (tier === null) return null;

    const tsMs = parseTsMs(record.ts);
    const latencyMs = parseFloat(record.latency_ms ?? "0") || 0;
    const ok = record.ok !== "false";
    const seq = record._seq ?? "";
    const particle: Particle = {
      id: `p-${server}-${seq}`,
      tier,
      server,
      seq,
      tool: record.tool ?? "",
      tsMs,
      latencyMs,
      ok,
    };

    if ((record.tool ?? "") === STATUS_TOOL) {
      // Tool=status records are the cascade-health channel. Only an explicit
      // boolean `available` flips the per-tier flag; anything else leaves it
      // alone (so a malformed payload is conservatively "no signal", not a
      // false-alarm flip). lastSeenMs always advances on a status record so
      // panels can later show staleness.
      const avail = extractAvailable(record.result);
      if (avail !== null) health[tier].available = avail;
      health[tier].lastSeenMs = tsMs;
    }

    queue.push(particle);
    while (queue.length > ceiling) queue.shift();

    const bucketKey = Math.floor(tsMs / 1000);
    const tierBuckets = buckets[tier];
    tierBuckets.set(bucketKey, (tierBuckets.get(bucketKey) ?? 0) + 1);
    pruneBuckets(tierBuckets, bucketKey);

    // SD-P2: this tier is "active" -- the panel/zone reads this to pulse.
    // Updated on every particle (including status records) since the user's
    // wish was "obvious when a node is processing", which is broader than
    // "produced a draftable particle for the flow river".
    lastIngest[tier] = tsMs;

    recent = { particle, record };
    totalParticles += 1;
    return particle;
  }

  function ingestCascadeOutcome(record: Record<string, string>): void {
    const finalTier = record.final_tier ?? "";
    // Defensive: only count records with a known final_tier. Unknown values
    // (future tier additions, malformed records) are ignored, not crash.
    let counted = false;
    if (finalTier === "npu") {
      resolvedNpu += 1;
      counted = true;
    } else if (finalTier === "igpu") {
      resolvedIgpu += 1;
      counted = true;
    } else if (finalTier === "gpu") {
      resolvedGpu += 1;
      counted = true;
    } else if (finalTier === "capped->tier3") {
      cappedRuns += 1;
      counted = true;
    }
    if (!counted) return;
    totalRuns += 1;
    // Skip rate is independent of outcome -- "the router decided not to try
    // Tier 1" can co-occur with any final_tier (including a successful gpu).
    if ((record.trace ?? "").includes("draft skipped")) {
      draftSkippedRuns += 1;
    }
  }

  function ingestDegen(record: Record<string, string>): void {
    const tier = record.tier;
    if (!isDegenTier(tier)) return;
    const score = Number.parseFloat(record.score ?? "");
    if (!Number.isFinite(score)) return;
    const obs: DegenObservation = {
      tsMs: parseTsMs(record.ts),
      tier,
      score,
      degraded: record.degraded === "true",
      reasons: parseReasons(record.reasons),
    };
    const log = degenLog[tier];
    log.push(obs);
    while (log.length > degenCeiling) log.shift();
  }

  function sparkline(tier: Tier, nowMs: number): readonly number[] {
    const endBucket = Math.floor(nowMs / 1000);
    const out: number[] = new Array(WINDOW_SECONDS) as number[];
    const tierBuckets = buckets[tier];
    for (let i = 0; i < WINDOW_SECONDS; i++) {
      const key = endBucket - (WINDOW_SECONDS - 1 - i);
      out[i] = tierBuckets.get(key) ?? 0;
    }
    return out;
  }

  return {
    ingest,
    particles: () => queue,
    sparkline,
    mostRecent: () => recent,
    spend: () => ({
      cloudCalls,
      usd,
      clean: cloudCalls === 0 && usd === 0,
    }),
    health: () => {
      let degraded = false;
      const tiers: Record<Tier, TierHealth> = {
        npu: { ...health.npu },
        gpu: { ...health.gpu },
        verify: { ...health.verify },
        cloud: { ...health.cloud },
      };
      for (const t of TIERS) {
        if (!tiers[t].available) degraded = true;
      }
      return { tiers, degraded };
    },
    // .slice() so callers get a snapshot, matching the contract of
    // particles()/health()/spend() (those also return fresh copies, not
    // live references into the store). Cheap at degenCeiling=30.
    degen: (tier: DegenTier) => degenLog[tier].slice(),
    cascadeOutcomes: () => ({
      resolvedNpu,
      resolvedIgpu,
      resolvedGpu,
      capped: cappedRuns,
      draftSkipped: draftSkippedRuns,
      total: totalRuns,
      effectivenessPct:
        totalRuns === 0
          ? 0
          : ((resolvedNpu + resolvedIgpu + resolvedGpu) / totalRuns) * 100,
    }),
    lastIngestMs: (tier: Tier) => lastIngest[tier],
    totalCount: () => totalParticles,
  };
}

function isDegenTier(s: string | undefined): s is DegenTier {
  return s !== undefined && (DEGEN_TIERS as readonly string[]).includes(s);
}

/** Parse the JSON-encoded reasons array. Returns an empty list on any
 * unparseable payload — a malformed reasons field downgrades the obs to
 * "no annotation", never throws. Mirrors `extractAvailable` defensiveness. */
function parseReasons(raw: string | undefined): readonly string[] {
  if (raw === undefined) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) {
      return parsed as readonly string[];
    }
  } catch {
    return [];
  }
  return [];
}

function parseTsMs(raw: string | undefined): number {
  if (raw === undefined) return Date.now();
  const seconds = Number.parseFloat(raw);
  if (!Number.isFinite(seconds)) return Date.now();
  return Math.round(seconds * 1000);
}

/** Pull `available` out of the JSON-encoded `result` field on a `.rec` record.
 * Returns null when there is no usable signal (missing field, non-boolean,
 * malformed JSON, or an absent `result`). Mirrors `extractCostUsd`'s
 * defensive pattern -- never throws. */
function extractAvailable(rawResult: string | undefined): boolean | null {
  if (rawResult === undefined) return null;
  try {
    const parsed = JSON.parse(rawResult) as unknown;
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      "available" in parsed
    ) {
      const v = (parsed as { available: unknown }).available;
      if (typeof v === "boolean") return v;
    }
  } catch {
    return null;
  }
  return null;
}

/** Pull `est_cost_usd` out of the JSON-encoded `result` field on edge-cloud
 * records. Mirrors the Python dashboard's heuristic. Never throws -- a
 * malformed payload contributes zero, never tilts the invariant. */
function extractCostUsd(rawResult: string | undefined): number {
  if (rawResult === undefined) return 0;
  try {
    const parsed = JSON.parse(rawResult) as unknown;
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      "est_cost_usd" in parsed
    ) {
      const v = (parsed as { est_cost_usd: unknown }).est_cost_usd;
      if (typeof v === "number" && Number.isFinite(v) && v > 0) return v;
    }
  } catch {
    return 0;
  }
  return 0;
}

/** Drop buckets older than the window so the per-tier map doesn't grow
 * unbounded. Called from `ingest`, so pruning happens at the same cadence
 * as inserts -- there's no stand-alone timer. */
function pruneBuckets(map: Map<number, number>, latestKey: number): void {
  const cutoff = latestKey - WINDOW_SECONDS;
  for (const key of map.keys()) {
    if (key < cutoff) map.delete(key);
  }
}
