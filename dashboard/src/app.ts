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

import {
  cascadeFlowRegion,
  cascadeFlowTopologyRegion,
  cascadeSpinRegion,
  hasActiveAnimation,
  HEARTBEAT_MS,
  LIVE,
  setTopologyGraph,
  TOPOLOGY,
} from "./flow.js";
import { page } from "./page.js";
import {
  cascadeHealthRegion,
  degenPanelRegion,
  logFeedRegion,
  meshEffectivenessRegion,
  nowPlayingRegion,
  rateMeterRegion,
  TICK,
} from "./panels.js";
import { CASCADE_SERVER, createStore, DEGEN_SERVER } from "./store.js";
import type { Store } from "./store.js";
import { createTailer } from "./lib/tailer.js";
import type { Tailer } from "./lib/tailer.js";
import { createLiveSource } from "./lib/liveSource.js";
import type { LiveSource } from "./lib/liveSource.js";

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
  /** The liveness lane: subscribes the Redis node-state channel + seeds on
   * start, driving the spinning ring by push. Started in server.ts. */
  readonly liveSource: LiveSource;
  /** Render the initial HTTP shell. */
  page(): VNode;
  /** Clear all in-memory state and push a TICK so all connected clients
   * re-render to the empty state. The tailer continues from its current
   * file position; only new records are ingested after a reset. */
  reset(): void;
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
  /** SD-P3 heartbeat: injectable setTimeout so vitest can drive the tick
   * chain deterministically without booting fake timers. Defaults to
   * globalThis setTimeout in production. */
  readonly scheduleTimer?: (cb: () => void, ms: number) => unknown;
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

  // SD-P3 heartbeat: between record arrivals, particles ride their entering
  // arcs (ANIM_MS), nodes glow while hot (HOT_MS), and the win/lose flash
  // holds (FLASH_MS). Without a heartbeat the next render only happens when a
  // new record lands, so an idle moment freezes mid-animation. The scheduler
  // self-loops at HEARTBEAT_MS while `hasActiveAnimation` is true and stops
  // naturally once the last in-flight thing settles -- an idle dashboard
  // issues zero ticks.
  const scheduleTimer = options.scheduleTimer ??
    ((cb: () => void, ms: number) => setTimeout(cb, ms));
  let heartbeatHandle: unknown = null;
  const maybeScheduleHeartbeat = (): void => {
    if (heartbeatHandle !== null) return; // single-flight: one tick in flight
    const now = ctx.nowMs();
    if (!hasActiveAnimation(
      store.particles(),
      store.lastOutcome(),
      now,
    )) return;
    heartbeatHandle = scheduleTimer(() => {
      heartbeatHandle = null;
      hub.emit(TICK);
      maybeScheduleHeartbeat();
    }, HEARTBEAT_MS);
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
        // After the record-driven render, kick the heartbeat so the new
        // particle's in-flight arc and the SD-P2 pulse window keep frames
        // flowing until they expire.
        maybeScheduleHeartbeat();
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
        logFeedRegion,
      );
      // Liveness lane on its own signal -- ledger TICKs never touch the spin
      // region, so the ring's animation isn't restarted by particle/hot renders.
      hub.subscribe(LIVE, conn, cascadeSpinRegion);
      // Topology lane: Beat-pushed graph updates re-render only the SVG shell.
      hub.subscribe(TOPOLOGY, conn, cascadeFlowTopologyRegion);
    },
    onMessage: () => {
      // Phase A page has no forms / actions; ignore any inbound frame.
    },
    onClose(conn) {
      hub.remove(conn);
    },
  });

  // Liveness lane: a node-state change re-renders ONLY the spin region via its
  // own LIVE signal -- never TICK -- so the ledger lane and the liveness lane
  // stay decoupled (no flicker either direction). The spin is CSS-continuous,
  // so it needs no heartbeat. Construction is side-effect-free (no redis client
  // until start()).
  const liveSource = createLiveSource({
    store,
    onChange: () => {
      hub.emit(LIVE);
    },
    onTopologyChange: (payload: unknown) => {
      setTopologyGraph(payload);
      hub.emit(TOPOLOGY);  // re-render static SVG shell
      hub.emit(TICK);       // sync overlays (hot rings, particles) to new positions
    },
  });

  return {
    ctx,
    vws,
    tailer,
    liveSource,
    page: () => page(ctx),
    reset(): void {
      ctx.store.reset();
      hub.emit(TICK);
    },
  };
}
