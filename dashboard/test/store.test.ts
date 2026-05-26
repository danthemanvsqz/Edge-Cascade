import { describe, expect, it } from "vitest";

import { createStore, serverToTier, WINDOW_SECONDS } from "../src/store.js";

function rec(
  seq: number,
  fields: Partial<{
    server: string;
    tool: string;
    ts: string;
    latency_ms: string;
    ok: string;
    result: string;
  }> & Record<string, string> = {},
): Record<string, string> {
  return { _seq: String(seq), ...fields };
}

describe("serverToTier", () => {
  it("maps the four canonical .rec servers to tier labels", () => {
    expect(serverToTier("edge-npu")).toBe("npu");
    expect(serverToTier("edge-gpu")).toBe("gpu");
    expect(serverToTier("edge-verify")).toBe("verify");
    expect(serverToTier("edge-cloud")).toBe("cloud");
  });

  it("returns null for experiment lanes and unknown servers", () => {
    expect(serverToTier("experiment-cp5-detector-calibration-2026-05-26")).toBeNull();
    expect(serverToTier("edge-image")).toBeNull(); // not in the Phase A river
    expect(serverToTier("")).toBeNull();
  });
});

describe("createStore — ingest + particles", () => {
  it("returns a stable particle id and tier for a known server", () => {
    const store = createStore();
    const p = store.ingest("edge-npu", rec(7, { tool: "route", ts: "1779801311.5", latency_ms: "42.5" }));
    expect(p).toEqual({
      id: "p-edge-npu-7",
      tier: "npu",
      server: "edge-npu",
      seq: "7",
      tool: "route",
      tsMs: Math.round(1779801311.5 * 1000),
      latencyMs: 42.5,
      ok: true,
    });
  });

  it("ignores records from unknown servers (returns null, no particle queued)", () => {
    const store = createStore();
    expect(store.ingest("experiment-foo", rec(0, { tool: "generate" }))).toBeNull();
    expect(store.particles()).toEqual([]);
    expect(store.totalCount()).toBe(0);
  });

  it("preserves arrival order in the particle queue", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "route" }));
    store.ingest("edge-gpu", rec(0, { tool: "generate" }));
    store.ingest("edge-verify", rec(0, { tool: "verify_functional" }));
    expect(store.particles().map((p) => p.id)).toEqual([
      "p-edge-npu-0",
      "p-edge-gpu-0",
      "p-edge-verify-0",
    ]);
  });

  it("enforces the particle ceiling (drops oldest first)", () => {
    const store = createStore({ particleCeiling: 3 });
    for (let i = 0; i < 5; i++) {
      store.ingest("edge-npu", rec(i, { tool: "route" }));
    }
    expect(store.particles().map((p) => p.seq)).toEqual(["2", "3", "4"]);
    expect(store.totalCount()).toBe(5); // total ingested -- not the live queue length
  });

  it("treats `ok=false` as failure; default is success", () => {
    const store = createStore();
    const a = store.ingest("edge-gpu", rec(0, { ok: "false" }));
    const b = store.ingest("edge-gpu", rec(1));
    expect(a?.ok).toBe(false);
    expect(b?.ok).toBe(true);
  });

  it("falls back to Date.now() when `ts` is missing/unparseable", () => {
    const store = createStore();
    const before = Date.now();
    const p = store.ingest("edge-npu", rec(0, { tool: "route" }));
    const after = Date.now();
    expect(p?.tsMs).toBeGreaterThanOrEqual(before);
    expect(p?.tsMs).toBeLessThanOrEqual(after);
  });
});

describe("createStore — sparkline", () => {
  it("returns exactly WINDOW_SECONDS buckets, oldest -> newest", () => {
    const store = createStore();
    const buckets = store.sparkline("npu", 100_000);
    expect(buckets.length).toBe(WINDOW_SECONDS);
    expect(buckets.every((b) => b === 0)).toBe(true);
  });

  it("buckets records by their `ts` second", () => {
    const store = createStore();
    // ts = 100 seconds, plus 99.5 and 99 -- three different 1-second buckets.
    store.ingest("edge-npu", rec(0, { ts: "100.0" }));
    store.ingest("edge-npu", rec(1, { ts: "100.7" }));
    store.ingest("edge-npu", rec(2, { ts: "99.5" }));
    store.ingest("edge-npu", rec(3, { ts: "99.0" }));
    const buckets = store.sparkline("npu", 100 * 1000); // nowMs ending at bucket 100
    // Buckets 99 and 100 are the last two entries.
    expect(buckets[WINDOW_SECONDS - 1]).toBe(2); // 100s
    expect(buckets[WINDOW_SECONDS - 2]).toBe(2); // 99s
    expect(buckets.slice(0, WINDOW_SECONDS - 2).every((b) => b === 0)).toBe(true);
  });

  it("zero-pads buckets outside the visible window", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { ts: "100" }));
    // Now we ask for a window ending 5 minutes later -- the original record
    // is outside the 60s window and should not appear.
    const buckets = store.sparkline("npu", (100 + 300) * 1000);
    expect(buckets.every((b) => b === 0)).toBe(true);
  });

  it("partitions by tier", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { ts: "100" }));
    store.ingest("edge-gpu", rec(0, { ts: "100" }));
    expect(store.sparkline("npu", 100_000)[WINDOW_SECONDS - 1]).toBe(1);
    expect(store.sparkline("gpu", 100_000)[WINDOW_SECONDS - 1]).toBe(1);
    expect(store.sparkline("verify", 100_000).every((b) => b === 0)).toBe(true);
  });
});

describe("createStore — spend (the load-bearing invariant)", () => {
  it("starts clean: zero calls, $0, clean=true", () => {
    expect(createStore().spend()).toEqual({
      cloudCalls: 0,
      usd: 0,
      clean: true,
    });
  });

  it("does NOT count edge-cloud status/budget calls toward cloudCalls", () => {
    const store = createStore();
    store.ingest("edge-cloud", rec(0, { tool: "status" }));
    store.ingest("edge-cloud", rec(1, { tool: "budget" }));
    expect(store.spend().cloudCalls).toBe(0);
    expect(store.spend().clean).toBe(true);
  });

  it("counts `ask` and `generate` calls; flips `clean` to false", () => {
    const store = createStore();
    store.ingest("edge-cloud", rec(0, { tool: "generate" }));
    store.ingest("edge-cloud", rec(1, { tool: "ask" }));
    const s = store.spend();
    expect(s.cloudCalls).toBe(2);
    expect(s.clean).toBe(false);
  });

  it("sums `est_cost_usd` extracted from JSON-encoded result fields", () => {
    const store = createStore();
    store.ingest(
      "edge-cloud",
      rec(0, {
        tool: "ask",
        result: JSON.stringify({ ok: true, est_cost_usd: 0.012 }),
      }),
    );
    store.ingest(
      "edge-cloud",
      rec(1, {
        tool: "generate",
        result: JSON.stringify({ ok: true, est_cost_usd: 0.05 }),
      }),
    );
    const s = store.spend();
    expect(s.cloudCalls).toBe(2);
    expect(s.usd).toBeCloseTo(0.062, 6);
    expect(s.clean).toBe(false);
  });

  it("ignores malformed result JSON without throwing", () => {
    const store = createStore();
    store.ingest(
      "edge-cloud",
      rec(0, { tool: "generate", result: "not json {" }),
    );
    expect(store.spend()).toEqual({ cloudCalls: 1, usd: 0, clean: false });
  });

  it("ignores negative / zero / NaN cost values", () => {
    const store = createStore();
    store.ingest(
      "edge-cloud",
      rec(0, { tool: "generate", result: JSON.stringify({ est_cost_usd: -1 }) }),
    );
    store.ingest(
      "edge-cloud",
      rec(1, { tool: "generate", result: JSON.stringify({ est_cost_usd: "nope" }) }),
    );
    expect(store.spend().usd).toBe(0);
  });
});

describe("createStore — mostRecent", () => {
  it("returns null initially", () => {
    expect(createStore().mostRecent()).toBeNull();
  });

  it("tracks the last record from any known tier (with the raw record map)", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "route" }));
    const fields = rec(0, { tool: "generate", result: '{"x":1}' });
    store.ingest("edge-gpu", fields);
    const r = store.mostRecent();
    expect(r?.particle.tier).toBe("gpu");
    expect(r?.record).toBe(fields); // same reference -- not copied
  });

  it("does not advance on records from unknown servers", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "route" }));
    store.ingest("experiment-foo", rec(0, { tool: "anything" }));
    expect(store.mostRecent()?.particle.server).toBe("edge-npu");
  });
});
