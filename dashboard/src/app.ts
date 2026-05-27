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

import { cascadeFlowRegion } from "./flow.js";
import { page } from "./page.js";
import {
  cascadeHealthRegion,
  degenPanelRegion,
  meshEffectivenessRegion,
  nowPlayingRegion,
  rateMeterRegion,
  TICK,
} from "./panels.js";
import { CASCADE_SERVER, createStore, DEGEN_SERVER } from "./store.js";
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
  /** Pass-through to the tailer's `startFromEof`. SD-3: when the dashboard is
   * auto-launched by `scripts/edge-cli.ps1` this is true, so the renderer only
   * shows records from THIS session, not whatever history `runs/` carries. */
  readonly startFromEof?: boolean;
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
    startFromEof: options.startFromEof,
    onRecord: ({ server, record }) => {
      const particle = store.ingest(server, record);
      // Emit on (a) any particle (the live cascade-flow + rate meter need
      // it) OR (b) any sidelane record (SD-2b degen panel, SD-4 effectiveness
      // panel) -- those panels paint from queues that `ingest` populates but
      // doesn't surface via the return value. Experiment lanes and unknown
      // servers still emit nothing.
      if (
        particle !== null ||
        server === DEGEN_SERVER ||
        server === CASCADE_SERVER
      ) {
        hub.emit(TICK);
      }
    },
  });

  const vws = createWSServer<DashContext>({
    path: "/ws",
    context: () => ctx,
    onConnect(conn) {
      hub.subscribe(
        TICK,
        conn,
        nowPlayingRegion,
        rateMeterRegion,
        cascadeHealthRegion,
        cascadeFlowRegion,
        degenPanelRegion,
        meshEffectivenessRegion,
      );
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
