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
import type { CascadeOutcomes, DegenObservation, DegenTier, Tier } from "./store.js";

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

/** Cascade health: one badge per tier + a container-level `degraded` class
 * that flips yellow on any tier's most-recent status reporting
 * `available:false`. Closes the Phase A visibility gap where NPU was down
 * the entire build and the dashboard had no surface for it. */
const HEALTH_TIERS: readonly Tier[] = ["npu", "gpu", "verify", "cloud"];

export const cascadeHealthRegion: LiveRegion<DashContext> = liveRegion(
  "cascade-health",
  (ctx) => {
    const report = ctx.store.health();
    const cls = report.degraded ? "cascade-health degraded" : "cascade-health ok";
    return h(
      "div",
      { class: cls },
      ...HEALTH_TIERS.map((t) => {
        const s = report.tiers[t];
        const state =
          s.lastSeenMs === null ? "unseen" : s.available ? "up" : "down";
        return h(
          "span",
          { class: `tier-health ${state}`, "data-tier": t, title: state },
          t,
        );
      }),
    );
  },
);

/** SD-4: mesh effectiveness gauge. Cumulative cascade outcomes this session
 * surfaced as four counts + a single percentage headline. Sourced from
 * `cascade.rec` records (one per mesh.solve Outcome).
 *
 * Layout: a header row with the big effectiveness % + total runs, then four
 * small "chips" -- resolved at NPU, resolved at GPU, capped to Tier 3, draft
 * skipped. Skip count is informational (sibling to the outcome chips, not
 * a fifth outcome) because a single run can be skipped AND resolved.
 * Tints: header turns red when effectiveness < 50% AND total >= 5
 * (small-sample guard so an early miss doesn't flash alarm). */
export const meshEffectivenessRegion: LiveRegion<DashContext> = liveRegion(
  "mesh-effectiveness",
  (ctx) => {
    const o = ctx.store.cascadeOutcomes();
    return meshEffectivenessView(o);
  },
);

/** Pure renderer separated for unit-testing without a full DashContext. */
export function meshEffectivenessView(o: CascadeOutcomes): VNode {
  if (o.total === 0) {
    return h(
      "div",
      { class: "mesh-eff empty" },
      h("span", { class: "mesh-eff-label" }, "mesh effectiveness"),
      h("span", { class: "mesh-eff-pct" }, "—"),
      h("span", { class: "mesh-eff-note" }, "no runs yet"),
    );
  }
  // Small-sample guard: don't flash alarm on a single early failure.
  const alarm = o.effectivenessPct < 50 && o.total >= 5;
  const headerCls = alarm ? "mesh-eff alarm" : "mesh-eff ok";
  return h(
    "div",
    { class: headerCls },
    h(
      "div",
      { class: "mesh-eff-header" },
      h("span", { class: "mesh-eff-label" }, "mesh effectiveness"),
      h("span", { class: "mesh-eff-pct" }, `${o.effectivenessPct.toFixed(1)}%`),
      h("span", { class: "mesh-eff-total" }, `${String(o.total)} runs`),
    ),
    h(
      "div",
      { class: "mesh-eff-chips" },
      chip("resolved-npu", "@NPU", o.resolvedNpu),
      chip("resolved-gpu", "@GPU", o.resolvedGpu),
      chip("capped", "capped", o.capped),
      chip("skipped", "skipped", o.draftSkipped),
    ),
  );
}

function chip(kind: string, label: string, count: number): VNode {
  return h(
    "span",
    { class: `mesh-eff-chip ${kind}`, "data-count": String(count) },
    h("span", { class: "chip-label" }, label),
    h("span", { class: "chip-count" }, String(count)),
  );
}

/** SD-2b: PD-1 v1 degeneration panel. One row per draft tier (NPU/GPU/iGPU);
 * each row paints (a) a score-history bar — discrete vertical bars, oldest
 * left, newest right; height = score in [0,1]; degraded obs are tinted
 * red — (b) a "tripped count" of degraded observations this session, and
 * (c) the most recent reason tag. Reads only the store; pure derivation.
 *
 * Why discrete bars not a smoothed sparkline: PD-1 observations are bursty
 * (one per draft, ~3–5 per solve), not regular-cadence. Honestly
 * rendering the burstiness keeps the over-trip noise instructive —
 * `docs/FINDINGS-pd1-v1-runtime-verification.md` proved the thresholds
 * are prose-calibrated and over-trip on code, so the visible warning is
 * itself the value. */
const DEGEN_PANEL_TIERS: readonly DegenTier[] = ["npu", "gpu", "igpu"];

export const degenPanelRegion: LiveRegion<DashContext> = liveRegion(
  "degen-panel",
  (ctx) => {
    return h(
      "div",
      { class: "degen-panel" },
      ...DEGEN_PANEL_TIERS.map((t) => degenRow(ctx, t)),
    );
  },
);

function degenRow(ctx: DashContext, tier: DegenTier): VNode {
  const obs = ctx.store.degen(tier);
  if (obs.length === 0) {
    return h(
      "div",
      { class: "degen-row empty", "data-tier": tier },
      h("span", { class: "badge tier" }, tier),
      h("span", { class: "degen-status" }, "no obs yet"),
    );
  }
  const trippedCount = obs.reduce(
    (n, o) => (o.degraded ? n + 1 : n),
    0,
  );
  const last = obs[obs.length - 1]!;
  const lastReason = last.reasons[0] ?? (last.degraded ? "degraded" : "clean");
  const rowCls = last.degraded ? "degen-row degraded" : "degen-row ok";
  return h(
    "div",
    { class: rowCls, "data-tier": tier },
    h("span", { class: "badge tier" }, tier),
    degenBars(obs),
    h(
      "span",
      { class: "degen-tripped", title: "degraded observations this session" },
      `${String(trippedCount)}/${String(obs.length)}`,
    ),
    h("code", { class: "degen-reason" }, lastReason),
  );
}

/** Score-history bars as ONE inline SVG. Geometry is fixed (60 wide, 20
 * tall) so the row layout is stable regardless of how many obs are in the
 * log. Each bar is 1 unit wide with 1 unit gap; the log occupies the
 * rightmost slots so the newest bar is always at x≈58. */
function degenBars(obs: readonly DegenObservation[]): VNode {
  const W = 60;
  const H = 20;
  const slotW = 2; // 1px bar + 1px gap
  const slots = Math.floor(W / slotW); // 30 slots in a 60-wide region
  const visible = obs.slice(-slots);
  const startX = W - visible.length * slotW;
  return h(
    "svg",
    {
      class: "degen-bars",
      viewBox: `0 0 ${String(W)} ${String(H)}`,
      width: String(W),
      height: String(H),
      "aria-hidden": "true",
    },
    ...visible.map((o, i) => {
      const x = startX + i * slotW;
      const barH = Math.max(1, Math.round(o.score * H));
      const y = H - barH;
      const cls = o.degraded ? "degen-bar degraded" : "degen-bar ok";
      return h("rect", {
        class: cls,
        x: String(x),
        y: String(y),
        width: "1",
        height: String(barH),
      });
    }),
  );
}

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
