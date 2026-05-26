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
  /** Total particles seen across all tiers (for the rate meter). */
  totalCount(): number;
}

export interface CreateStoreOptions {
  /** Max particles retained in the queue. Default 200. */
  readonly particleCeiling?: number;
}

export const WINDOW_SECONDS = 60;
const DEFAULT_CEILING = 200;
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

    recent = { particle, record };
    totalParticles += 1;
    return particle;
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
    totalCount: () => totalParticles,
  };
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
