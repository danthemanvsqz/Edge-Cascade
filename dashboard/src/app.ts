/**
 * App factory -- the wiring layer between the tailer (records in), the store
 * (state), Vinyl's SignalHub (push fabric), and the page renderer (HTTP/WS).
 *
 * Construction is side-effect-free: no ports are bound, no files are read.
 * The caller (server.ts in production, vitest in unit tests) decides when to
 * `tailer.start()` and when to listen. That keeps the integration tests fast
 * and the production entry point thin.
 */
import {
  createSignalHub,
  createWSServer,
} from "@danthemanvsqz/vinyl";
import type {
  SignalHub,
  VNode,
  VinylWSServer,
} from "@danthemanvsqz/vinyl";

import { page } from "./page.js";
import { nowPlayingRegion, rateMeterRegion, TICK } from "./panels.js";
import { createStore } from "./store.js";
import type { Store } from "./store.js";
import { createTailer } from "./lib/tailer.js";
import type { Tailer } from "./lib/tailer.js";

/** Per-connection context. The store + hub are shared across all connections
 * (single-host dashboard) so every region renders from the same state. */
export interface DashContext {
  readonly store: Store;
  readonly hub: SignalHub<DashContext>;
  /** Injectable clock so tests can pin "now" for sparkline/rate-meter calls. */
  readonly nowMs: () => number;
}

export interface DashboardApp {
  readonly ctx: DashContext;
  readonly vws: VinylWSServer;
  readonly tailer: Tailer;
  /** Render the initial HTTP shell. */
  page(): VNode;
}

export interface CreateDashboardOptions {
  readonly runsDir: string;
  readonly particleCeiling?: number;
  readonly nowMs?: () => number;
  /** Poll interval for the tailer (passed straight through). Useful for
   * tests; production uses the default 250ms. */
  readonly tailerIntervalMs?: number;
}

export function createDashboardApp(
  options: CreateDashboardOptions,
): DashboardApp {
  const store = createStore({ particleCeiling: options.particleCeiling });
  const hub = createSignalHub<DashContext>();
  const ctx: DashContext = {
    store,
    hub,
    nowMs: options.nowMs ?? Date.now,
  };

  const tailer = createTailer({
    runsDir: options.runsDir,
    intervalMs: options.tailerIntervalMs,
    onRecord: ({ server, record }) => {
      const particle = store.ingest(server, record);
      // A record from an unknown lane (e.g. experiment-*) still changes the
      // store iff it was an edge-cloud spend event -- but those are caught
      // by serverToTier inside ingest, and unknown lanes don't touch state
      // at all. So only emit when a particle was produced.
      if (particle !== null) hub.emit(TICK);
    },
  });

  const vws = createWSServer<DashContext>({
    path: "/ws",
    context: () => ctx,
    onConnect(conn) {
      hub.subscribe(TICK, conn, nowPlayingRegion, rateMeterRegion);
    },
    onMessage: () => {
      // Phase A page has no forms / actions; ignore any inbound frame.
    },
    onClose(conn) {
      hub.remove(conn);
    },
  });

  return {
    ctx,
    vws,
    tailer,
    page: () => page(ctx),
  };
}
