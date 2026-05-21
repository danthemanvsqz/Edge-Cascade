/**
 * M5 — live regions + signals (the state model's render half).
 *
 * A *live region* is a named, re-renderable subtree. It renders inline in the
 * initial shell behind a stable `<vinyl-slot id>` (the same boundary wrapper
 * M3/M4 use) and can be re-rendered later from current DB state and pushed as
 * an `hx-swap-oob` frame — so the browser swaps it with zero client state.
 *
 *   - `mount(ctx)` → inline shell node `<vinyl-slot id>{render(ctx)}</vinyl-slot>`.
 *   - `frame(ctx)` → the OOB push string for the same id.
 *
 * Both render the SAME content the SAME way; the shell node and every frame
 * carry the same id, which the htmx-ws contract requires (ARCHITECTURE.md §7).
 *
 * Region renders are SYNCHRONOUS: the sweet spot is a server round-trip backed
 * by a synchronous DB (better-sqlite3). Async data for the *initial* paint is
 * Suspense's job (M3); live regions are for the post-action re-render + push.
 *
 * A `SignalHub` is the pub/sub half: connections subscribe their regions to a
 * signal key; an action mutates the DB and `emit(key)` re-renders + pushes to
 * every subscribed connection — the cross-client "live" update.
 */
import { h } from "./vnode.js";
import type { VNode, VNodeChild } from "./vnode.js";
import { BOUNDARY_TAG, oob } from "./oob.js";
import { renderToString } from "./render.js";
import { safeSeg } from "./ids.js";
import type { VinylConnection } from "./ws.js";

/** Stable, collision-free DOM id for a live region (distinct from `vinyl-s-*`
 * Suspense ids). Same `safeSeg` normalization as the M3 id scheme. */
export function regionId(key: string): string {
  return `vinyl-r-${safeSeg(key)}`;
}

export interface LiveRegion<C> {
  readonly key: string;
  readonly id: string;
  /** Pure render of the region's content from context (reads DB synchronously). */
  render(ctx: C): VNodeChild;
  /** Inline shell node: `<vinyl-slot id>{render(ctx)}</vinyl-slot>`. */
  mount(ctx: C): VNode;
  /** OOB push string: `oob(id, html)` for the same id. */
  frame(ctx: C): string;
}

export function liveRegion<C>(
  key: string,
  render: (ctx: C) => VNodeChild,
): LiveRegion<C> {
  const id = regionId(key);
  return {
    key,
    id,
    render,
    mount(ctx) {
      return h(BOUNDARY_TAG, { id }, render(ctx));
    },
    frame(ctx) {
      return oob(id, renderToString(render(ctx)));
    },
  };
}

interface Subscription<C> {
  conn: VinylConnection<C>;
  regions: LiveRegion<C>[];
}

export interface SignalHub<C> {
  /** Bind a connection's region(s) to a signal key. Returns an unsubscribe fn. */
  subscribe(
    key: string,
    conn: VinylConnection<C>,
    ...regions: LiveRegion<C>[]
  ): () => void;
  /** Re-render + push every subscribed connection's regions for `key`. */
  emit(key: string): void;
  /** Drop all of a connection's subscriptions (call from `onClose`). */
  remove(conn: VinylConnection<C>): void;
  /** Total subscription records across all keys. */
  readonly size: number;
}

export function createSignalHub<C>(): SignalHub<C> {
  const byKey = new Map<string, Set<Subscription<C>>>();

  return {
    subscribe(key, conn, ...regions) {
      const sub: Subscription<C> = { conn, regions };
      let set = byKey.get(key);
      if (!set) {
        set = new Set<Subscription<C>>();
        byKey.set(key, set);
      }
      set.add(sub);
      return () => {
        set.delete(sub);
        if (set.size === 0) byKey.delete(key);
      };
    },

    emit(key) {
      const set = byKey.get(key);
      if (!set) return;
      // One coalesced frame per connection (htmx applies all OOB elements in
      // one settle pass — see ARCHITECTURE.md §7).
      for (const sub of set) {
        sub.conn.push(...sub.regions.map((r) => r.frame(sub.conn.context)));
      }
    },

    remove(conn) {
      for (const [key, set] of byKey) {
        for (const sub of [...set]) {
          if (sub.conn === conn) set.delete(sub);
        }
        if (set.size === 0) byKey.delete(key);
      }
    },

    get size() {
      let total = 0;
      for (const set of byKey.values()) total += set.size;
      return total;
    },
  };
}
