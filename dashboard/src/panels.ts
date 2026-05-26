/**
 * Live regions for the Phase-A skeleton. Slice 5 wires two regions end-to-end
 * (now-playing + rate meter); Slice 6 will add the cascade-flow particle
 * region + per-tier sparklines/stats. A single signal `tick` is the only
 * key for now: the tailer emits it after every accepted record, every
 * subscribed region re-renders + pushes. Coalescing per signal key is a
 * Phase-B item; at 5-50 rec/s the coarse fan-out is acceptable.
 */
import { h, liveRegion } from "@danthemanvsqz/vinyl";
import type { LiveRegion, VNode } from "@danthemanvsqz/vinyl";

import type { DashContext } from "./app.js";

/** The single signal that drives every region (for now). */
export const TICK = "tick";

/** "Now playing": the most recent record's tier + tool + latency + ok state.
 * Reads the store synchronously -- the live-region rule (M5: sync renders). */
export const nowPlayingRegion: LiveRegion<DashContext> = liveRegion(
  "now-playing",
  (ctx) => {
    const r = ctx.store.mostRecent();
    if (!r) {
      return h("div", { class: "now-playing empty" }, "waiting for activity…");
    }
    const p = r.particle;
    const args = truncate(r.record.args ?? "", 120);
    const cls = p.ok ? "now-playing ok" : "now-playing fail";
    return h(
      "div",
      { class: cls, "data-tier": p.tier },
      h("span", { class: "badge tier" }, p.tier),
      h("span", { class: "tool" }, p.tool),
      h("span", { class: "latency" }, `${formatMs(p.latencyMs)}`),
      h("code", { class: "args" }, args),
    );
  },
);

/** Header rate meter: total particles + records/sec over the live window. */
export const rateMeterRegion: LiveRegion<DashContext> = liveRegion(
  "rate-meter",
  (ctx) => {
    const total = ctx.store.totalCount();
    const rate = recordsPerSecond(ctx);
    return h(
      "div",
      { class: "rate-meter" },
      h("span", { class: "total" }, `${String(total)} records`),
      h("span", { class: "rate" }, `${rate.toFixed(1)} rec/s`),
      spendBadge(ctx),
    );
  },
);

/** Spend badge: red iff the local-only invariant has been broken. Standalone
 * so the SVG header can drop it next to the rate meter without recomputing. */
function spendBadge(ctx: DashContext): VNode {
  const s = ctx.store.spend();
  return h(
    "span",
    { class: s.clean ? "spend clean" : "spend dirty" },
    `$${s.usd.toFixed(2)} · ${String(s.cloudCalls)} cloud`,
  );
}

/** Sum of all per-tier 1-second buckets within the window, divided by window
 * length. */
function recordsPerSecond(ctx: DashContext): number {
  const TIERS = ["npu", "gpu", "verify", "cloud"] as const;
  const nowMs = ctx.nowMs();
  let total = 0;
  for (const t of TIERS) {
    for (const v of ctx.store.sparkline(t, nowMs)) total += v;
  }
  return total / 60;
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

function formatMs(ms: number): string {
  if (ms === 0) return "—";
  if (ms < 1000) return `${ms.toFixed(0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}
