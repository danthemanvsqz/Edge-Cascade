/**
 * Live cascade-activity source -- the dashboard's liveness lane (no polling).
 *
 * Subscribes to the Redis pub/sub channel the Python receiver
 * (scripts/cascade_live_receiver.py) publishes node-state deltas on, and GETs
 * the current-active-set seed key once on start (pub/sub is fire-and-forget, so
 * a server that starts mid-solve would otherwise miss the deltas that already
 * fired). Each delta drives store.applyNodeDelta + onChange (hub.emit), so the
 * spinning ring updates by PUSH; nothing here or in the browser polls.
 *
 * Channel/key names mirror cascade/live_receiver.py (LIVE_CHANNEL /
 * LIVE_STATE_KEY). The pure message parsers are extracted + tested; the ioredis
 * wiring is glue, exercised live. `createClient` is injectable so tests drive it
 * with a fake redis (no broker, no network).
 */
import { Redis } from "ioredis";

import type { Store } from "../store.js";

export const LIVE_CHANNEL = "cascade.live.nodes";
export const LIVE_STATE_KEY = "cascade.live.active";

// Topology graph channel: Beat publishes {name, nodes[], edges[]} here on
// worker startup + every 30 s. Dashboard subscribes and calls onTopologyChange.
export const TOPOLOGY_CHANNEL = "cascade.live.topology";
export const TOPOLOGY_STATE_KEY = "cascade.live.topology.current";

const DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0";

/** Parse one pub/sub delta frame `{node, state}`. The receiver emits only
 * `state: "active" | "idle"` (cascade/live_receiver.py:node_delta), so anything
 * other than "active" is treated as idle. Never throws: a malformed or
 * non-object payload, or a missing/non-string node, yields null. */
export function parseDelta(msg: string): { node: string; active: boolean } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(msg);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object") return null;
  const obj = parsed as Record<string, unknown>;
  if (typeof obj.node !== "string") return null;
  return { node: obj.node, active: obj.state === "active" };
}

/** Parse the seed key payload (a JSON array of active node ids). Never throws:
 * null input, malformed JSON, or a non-array yields []. Non-string elements are
 * dropped. */
export function parseSeed(raw: string | null): string[] {
  if (raw === null) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter((x): x is string => typeof x === "string");
}

export interface LiveSource {
  start(): Promise<void>;
  stop(): Promise<void>;
}

export interface CreateLiveSourceOptions {
  readonly store: Store;
  /** Called after any active-node change (seed or delta) -- wired to
   * hub.emit(TICK) so the flow region re-renders by push. */
  readonly onChange: () => void;
  /** Called when a topology graph update arrives from the Beat task. The
   * argument is the raw parsed payload {name, nodes, edges}. Wire this to
   * setTopologyGraph() + hub.emit(TOPOLOGY) so the SVG live-region updates. */
  readonly onTopologyChange?: (payload: unknown) => void;
  readonly redisUrl?: string;
  /** Injectable client factory (default: a lazyConnect ioredis). Tests pass a
   * fake redis so no broker/network is touched. */
  readonly createClient?: (url: string) => Redis;
}

export function createLiveSource(opts: CreateLiveSourceOptions): LiveSource {
  const url = opts.redisUrl ?? process.env.CASCADE_REDIS_URL ?? DEFAULT_REDIS_URL;
  const make = opts.createClient ?? ((u: string): Redis => new Redis(u, { lazyConnect: true }));
  // Clients are created in start(), not here, so constructing the source is
  // side-effect-free (no connection, no timers) -- mirrors the tailer/app.ts.
  let sub: Redis | null = null;
  let seed: Redis | null = null;

  async function start(): Promise<void> {
    sub = make(url);
    seed = make(url);
    // Seed the current active-node set, then ride deltas.
    const seeded = parseSeed(await seed.get(LIVE_STATE_KEY));
    if (seeded.length > 0) {
      opts.store.setActiveNodes(seeded);
      opts.onChange();
    }
    // Seed the topology graph (best-effort; fall back to CHAIN_SPECS if absent).
    if (opts.onTopologyChange) {
      const topoRaw = await seed.get(TOPOLOGY_STATE_KEY);
      if (topoRaw) {
        try {
          opts.onTopologyChange(JSON.parse(topoRaw));
        } catch { /* malformed — ignore, keep CHAIN_SPECS fallback */ }
      }
    }
    sub.on("message", (channel: string, message: string) => {
      if (channel === TOPOLOGY_CHANNEL) {
        if (opts.onTopologyChange) {
          try {
            opts.onTopologyChange(JSON.parse(message));
          } catch { /* ignore */ }
        }
        return;
      }
      const delta = parseDelta(message);
      if (delta !== null) {
        opts.store.applyNodeDelta(delta.node, delta.active);
        opts.onChange();
      }
    });
    await sub.subscribe(LIVE_CHANNEL, TOPOLOGY_CHANNEL);
  }

  async function stop(): Promise<void> {
    // quit() on a never-connected / failed lazy client rejects -- swallow so
    // teardown is always safe even if start() failed partway.
    if (sub) await sub.quit().catch(() => undefined);
    if (seed) await seed.quit().catch(() => undefined);
    sub = null;
    seed = null;
  }

  return { start, stop };
}
