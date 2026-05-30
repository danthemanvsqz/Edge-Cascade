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

  it("filters tool=status health probes -- updates health, produces NO particle", () => {
    const store = createStore();
    const p = store.ingest(
      "edge-gpu",
      rec(0, { tool: "status", ts: "100", result: JSON.stringify({ available: false }) }),
    );
    // The probe must not pollute the flow / rate meter / log feed...
    expect(p).toBeNull();
    expect(store.particles()).toEqual([]);
    expect(store.totalCount()).toBe(0);
    // ...but it still drives the health side-channel.
    expect(store.health().tiers.gpu.available).toBe(false);
    expect(store.health().tiers.gpu.lastSeenMs).toBe(100000);
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

describe("createStore — health (cascade-visibility derivation)", () => {
  it("starts clean: every tier available, none seen, degraded:false", () => {
    const h = createStore().health();
    expect(h.degraded).toBe(false);
    for (const t of ["npu", "gpu", "verify", "cloud"] as const) {
      expect(h.tiers[t]).toEqual({ available: true, lastSeenMs: null });
    }
  });

  it("non-status records do NOT touch health (even if their result carries available:false)", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "route", ts: "100", result: JSON.stringify({ available: false }) }));
    const h = store.health();
    expect(h.degraded).toBe(false);
    expect(h.tiers.npu).toEqual({ available: true, lastSeenMs: null });
  });

  it("a status record with available:false flips that tier; degraded goes true", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "status", ts: "100", result: JSON.stringify({ available: false, reason: "model missing" }) }));
    const h = store.health();
    expect(h.tiers.npu).toEqual({ available: false, lastSeenMs: 100_000 });
    expect(h.tiers.gpu.available).toBe(true);
    expect(h.degraded).toBe(true);
  });

  it("a later status with available:true flips the tier back; degraded recovers", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "status", ts: "100", result: JSON.stringify({ available: false }) }));
    expect(store.health().degraded).toBe(true);
    store.ingest("edge-npu", rec(1, { tool: "status", ts: "200", result: JSON.stringify({ available: true, device: "NPU" }) }));
    const h = store.health();
    expect(h.tiers.npu).toEqual({ available: true, lastSeenMs: 200_000 });
    expect(h.degraded).toBe(false);
  });

  it("malformed / missing / non-boolean `available` leaves the flag alone but still advances lastSeenMs", () => {
    const store = createStore();
    // Seed an explicit-down state.
    store.ingest("edge-gpu", rec(0, { tool: "status", ts: "100", result: JSON.stringify({ available: false }) }));
    // Now a status with no parseable signal -- flag must stay false; lastSeenMs advances.
    store.ingest("edge-gpu", rec(1, { tool: "status", ts: "200", result: "not json {" }));
    store.ingest("edge-gpu", rec(2, { tool: "status", ts: "300", result: JSON.stringify({ device: "GPU" }) }));
    store.ingest("edge-gpu", rec(3, { tool: "status", ts: "400", result: JSON.stringify({ available: "yes" }) }));
    const h = store.health();
    expect(h.tiers.gpu.available).toBe(false);
    expect(h.tiers.gpu.lastSeenMs).toBe(400_000);
  });

  it("degraded reports true if ANY single tier is down", () => {
    const store = createStore();
    store.ingest("edge-cloud", rec(0, { tool: "status", ts: "1", result: JSON.stringify({ available: false }) }));
    expect(store.health().degraded).toBe(true);
    // Even though the other three are still up:
    expect(store.health().tiers.npu.available).toBe(true);
    expect(store.health().tiers.gpu.available).toBe(true);
    expect(store.health().tiers.verify.available).toBe(true);
  });

  it("status records from unknown servers do not synthesise tier health", () => {
    const store = createStore();
    store.ingest("experiment-foo", rec(0, { tool: "status", ts: "100", result: JSON.stringify({ available: false }) }));
    const h = store.health();
    expect(h.degraded).toBe(false);
    expect(h.tiers.npu.lastSeenMs).toBeNull();
  });

  it("each call to health() returns a fresh snapshot (mutating it does not affect the next read)", () => {
    const store = createStore();
    store.ingest("edge-npu", rec(0, { tool: "status", ts: "100", result: JSON.stringify({ available: false }) }));
    const first = store.health();
    // Try to tamper -- the returned object is treated as read-only by the
    // type system, but in practice .tiers.npu is a plain object; cast away
    // the readonly to confirm the next call yields a fresh copy.
    (first.tiers.npu as { available: boolean; lastSeenMs: number | null }).available = true;
    expect(store.health().tiers.npu.available).toBe(false);
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

// SD-2b: cascade-degeneration side-lane -----------------------------------

function degenRec(
  seq: number,
  fields: Partial<{
    ts: string;
    tier: string;
    score: string;
    degraded: string;
    reasons: string;
  }> = {},
): Record<string, string> {
  return {
    _seq: String(seq),
    tool: "observe",
    ts: "1779801311.5",
    tier: "npu",
    score: "0.17",
    degraded: "true",
    reasons: '["looping: trigram_repeat=0.10 > 0.04"]',
    ...fields,
  };
}

describe("createStore — degen lane", () => {
  it("ingests a cascade-degeneration record without producing a particle", () => {
    const store = createStore();
    const result = store.ingest("cascade-degeneration", degenRec(0));
    expect(result).toBeNull();
    expect(store.particles()).toEqual([]);
    expect(store.totalCount()).toBe(0);
  });

  it("appends observations to the per-tier log in arrival order", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { tier: "npu", score: "0.17" }));
    store.ingest("cascade-degeneration", degenRec(1, { tier: "gpu", score: "0.42" }));
    store.ingest("cascade-degeneration", degenRec(2, { tier: "npu", score: "0.25" }));
    expect(store.degen("npu").map((o) => o.score)).toEqual([0.17, 0.25]);
    expect(store.degen("gpu").map((o) => o.score)).toEqual([0.42]);
    expect(store.degen("igpu")).toEqual([]);
  });

  it("parses degraded='true'/'false' to a boolean", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { degraded: "true" }));
    store.ingest("cascade-degeneration", degenRec(1, { degraded: "false" }));
    const log = store.degen("npu");
    expect(log[0]?.degraded).toBe(true);
    expect(log[1]?.degraded).toBe(false);
  });

  it("parses reasons as a string array (JSON round-trip)", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, {
      reasons: '["looping: trigram_repeat=0.10 > 0.04", "tier:gpu unavailable"]',
    }));
    expect(store.degen("npu")[0]?.reasons).toEqual([
      "looping: trigram_repeat=0.10 > 0.04",
      "tier:gpu unavailable",
    ]);
  });

  it("returns an empty reasons array on malformed JSON (never throws)", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { reasons: "{not json" }));
    expect(store.degen("npu")[0]?.reasons).toEqual([]);
  });

  it("returns an empty reasons array on a non-array JSON payload", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { reasons: '"a string"' }));
    expect(store.degen("npu")[0]?.reasons).toEqual([]);
  });

  it("returns an empty reasons array on an array containing non-strings", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { reasons: '["ok", 42]' }));
    expect(store.degen("npu")[0]?.reasons).toEqual([]);
  });

  it("drops records whose tier is not a recognised draft tier", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { tier: "verify" }));
    store.ingest("cascade-degeneration", degenRec(1, { tier: "cloud" }));
    store.ingest("cascade-degeneration", degenRec(2, { tier: "" }));
    expect(store.degen("npu")).toEqual([]);
    expect(store.degen("gpu")).toEqual([]);
    expect(store.degen("igpu")).toEqual([]);
  });

  it("drops records whose score is not parseable", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { score: "" }));
    store.ingest("cascade-degeneration", degenRec(1, { score: "NaN" }));
    store.ingest("cascade-degeneration", degenRec(2, { score: "0.5" }));
    expect(store.degen("npu").map((o) => o.score)).toEqual([0.5]);
  });

  it("derives tsMs from the record's ts field (seconds → ms)", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { ts: "1779801311.5" }));
    expect(store.degen("npu")[0]?.tsMs).toBe(Math.round(1779801311.5 * 1000));
  });

  it("enforces degenCeiling per tier (oldest dropped first)", () => {
    const store = createStore({ degenCeiling: 3 });
    for (let i = 0; i < 5; i++) {
      store.ingest("cascade-degeneration", degenRec(i, { score: String(i / 10) }));
    }
    expect(store.degen("npu").map((o) => o.score)).toEqual([0.2, 0.3, 0.4]);
  });

  it("ignores degen records for spend accounting (clean stays true)", () => {
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0));
    expect(store.spend()).toEqual({ cloudCalls: 0, usd: 0, clean: true });
  });

  it("degen(tier) returns a snapshot, not a live reference", () => {
    // Matches the contract of particles()/health()/spend() (all return
    // fresh copies). A live reference would silently mutate under callers
    // that captured a previous snapshot -- the trap PR review #65 flagged.
    const store = createStore();
    store.ingest("cascade-degeneration", degenRec(0, { score: "0.10" }));
    const before = store.degen("npu");
    store.ingest("cascade-degeneration", degenRec(1, { score: "0.20" }));
    expect(before).toHaveLength(1);
    expect(store.degen("npu")).toHaveLength(2);
  });
});

// SD-4: cascade-outcomes side-lane (mesh effectiveness) ------------------

function cascadeRec(
  seq: number,
  fields: Partial<{ final_tier: string; trace: string }> = {},
): Record<string, string> {
  return {
    _seq: String(seq),
    tool: "solve",
    ts: "1779801311.5",
    final_tier: "gpu",
    trace: "mesh|-|0.00s|route difficulty=0.55\nmesh|-|0.00s|npu gate FAIL",
    ...fields,
  };
}

describe("createStore — cascade outcomes lane (SD-4)", () => {
  it("starts empty: every counter 0, effectiveness 0", () => {
    const o = createStore().cascadeOutcomes();
    expect(o).toEqual({
      resolvedNpu: 0,
      resolvedIgpu: 0,
      resolvedGpu: 0,
      capped: 0,
      draftSkipped: 0,
      npuGaveUp: 0,
      total: 0,
      effectivenessPct: 0,
    });
  });

  it("ingests a cascade record without producing a particle", () => {
    const store = createStore();
    const result = store.ingest("cascade", cascadeRec(0));
    expect(result).toBeNull();
    expect(store.particles()).toEqual([]);
    expect(store.totalCount()).toBe(0);
  });

  it("counts final_tier 'npu' as resolvedNpu (not rolled into igpu)", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "npu" }));
    const o = store.cascadeOutcomes();
    expect(o.resolvedNpu).toBe(1);
    expect(o.resolvedIgpu).toBe(0);
    expect(o.total).toBe(1);
  });

  it("counts final_tier 'igpu' as its own resolvedIgpu (not rolled into npu)", () => {
    // PR #71 review fix: igpu is Tier 1b, a distinct drafter (3B model on
    // Intel iGPU) -- conflating its wins into resolvedNpu silently
    // misattributes them in the @NPU chip.
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "igpu" }));
    const o = store.cascadeOutcomes();
    expect(o.resolvedIgpu).toBe(1);
    expect(o.resolvedNpu).toBe(0);
    expect(o.total).toBe(1);
  });

  it("counts final_tier 'gpu' as resolvedGpu", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "gpu" }));
    expect(store.cascadeOutcomes().resolvedGpu).toBe(1);
  });

  it("counts final_tier 'capped->tier3' as capped", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "capped->tier3" }));
    expect(store.cascadeOutcomes().capped).toBe(1);
  });

  it("counts 'draft skipped' in trace as a skipped run (independent of outcome)", () => {
    // One skipped run that still resolved at GPU: both counters increment.
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, {
      final_tier: "gpu",
      trace: "mesh|-|0.00s|npu draft skipped (difficulty>=0.7)\nmesh|-|0.00s|gpu gate PASS",
    }));
    const o = store.cascadeOutcomes();
    expect(o.resolvedGpu).toBe(1);
    expect(o.draftSkipped).toBe(1);
    expect(o.total).toBe(1);
  });

  it("counts 'npu gate FAIL' as an NPU-gave-up run (the NPU tried and lost)", () => {
    const store = createStore();
    // NPU drafted but failed the gate -> escalated and resolved at GPU.
    store.ingest("cascade", cascadeRec(0, {
      final_tier: "gpu",
      trace: "mesh|-|0.00s|npu draft -> 200 chars\nmesh|-|0.00s|npu gate FAIL\nmesh|-|0.00s|gpu gate PASS",
    }));
    expect(store.cascadeOutcomes().npuGaveUp).toBe(1);
    // A skip is NOT a give-up (it never tried).
    store.ingest("cascade", cascadeRec(1, {
      final_tier: "gpu",
      trace: "mesh|-|0.00s|npu draft skipped (difficulty>=0.7)",
    }));
    expect(store.cascadeOutcomes().npuGaveUp).toBe(1);
  });

  it("ignores records with unknown final_tier (does not increment total)", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "totally-bogus" }));
    expect(store.cascadeOutcomes().total).toBe(0);
  });

  it("ignores records missing final_tier (defensive: empty string)", () => {
    const store = createStore();
    store.ingest("cascade", { _seq: "0", tool: "solve", ts: "1779801311.5" });
    expect(store.cascadeOutcomes().total).toBe(0);
  });

  it("computes effectivenessPct as (resolved / total) * 100 across all draft tiers", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "npu" }));
    store.ingest("cascade", cascadeRec(1, { final_tier: "igpu" }));
    store.ingest("cascade", cascadeRec(2, { final_tier: "gpu" }));
    store.ingest("cascade", cascadeRec(3, { final_tier: "capped->tier3" }));
    const o = store.cascadeOutcomes();
    expect(o.total).toBe(4);
    // 3 resolved / 4 total -- npu + igpu + gpu all count toward effectiveness.
    expect(o.effectivenessPct).toBeCloseTo(75, 5);
  });

  it("ignores cascade records for spend accounting (clean stays true)", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0));
    expect(store.spend()).toEqual({ cloudCalls: 0, usd: 0, clean: true });
  });

  it("cascadeOutcomes() returns a fresh snapshot per call", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "gpu" }));
    const before = store.cascadeOutcomes();
    store.ingest("cascade", cascadeRec(1, { final_tier: "gpu" }));
    expect(before.total).toBe(1);
    expect(store.cascadeOutcomes().total).toBe(2);
  });
});

describe("createStore — lastOutcome (win/lose flash trigger)", () => {
  it("starts null (no outcome seen yet)", () => {
    expect(createStore().lastOutcome()).toBeNull();
  });

  it("marks a local-tier resolution as a win", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "gpu" }));
    const o = store.lastOutcome();
    expect(o?.won).toBe(true);
    expect(o?.finalTier).toBe("gpu");
    expect(o?.tsMs).toBe(1779801311500);
  });

  it("npu and igpu resolutions are wins too", () => {
    const npu = createStore();
    npu.ingest("cascade", cascadeRec(0, { final_tier: "npu" }));
    expect(npu.lastOutcome()?.won).toBe(true);
    const igpu = createStore();
    igpu.ingest("cascade", cascadeRec(0, { final_tier: "igpu" }));
    expect(igpu.lastOutcome()?.won).toBe(true);
  });

  it("marks a capped->tier3 takeover as a loss", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "capped->tier3" }));
    const o = store.lastOutcome();
    expect(o?.won).toBe(false);
    expect(o?.finalTier).toBe("capped->tier3");
  });

  it("bumps seq on each new outcome and tracks the most recent", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "gpu" }));
    const first = store.lastOutcome();
    store.ingest("cascade", cascadeRec(1, { final_tier: "capped->tier3" }));
    const second = store.lastOutcome();
    expect(first?.seq).toBe(1);
    expect(second?.seq).toBe(2);
    expect(second?.won).toBe(false);
  });

  it("ignores unknown final_tier (no outcome recorded)", () => {
    const store = createStore();
    store.ingest("cascade", cascadeRec(0, { final_tier: "totally-bogus" }));
    expect(store.lastOutcome()).toBeNull();
  });
});

describe("createStore — live active nodes (the spinning-ring lane)", () => {
  it("starts with an empty active set", () => {
    expect(createStore().activeNodes()).toEqual(new Set());
  });

  it("setActiveNodes seeds the full set, replacing any prior", () => {
    const store = createStore();
    store.setActiveNodes(["route", "draft"]);
    expect(store.activeNodes()).toEqual(new Set(["route", "draft"]));
    store.setActiveNodes(["gpu_solve"]);
    expect(store.activeNodes()).toEqual(new Set(["gpu_solve"]));
  });

  it("applyNodeDelta adds on active=true, removes on active=false", () => {
    const store = createStore();
    store.applyNodeDelta("gpu_solve", true);
    store.applyNodeDelta("route", true);
    expect(store.activeNodes()).toEqual(new Set(["gpu_solve", "route"]));
    store.applyNodeDelta("gpu_solve", false);
    expect(store.activeNodes()).toEqual(new Set(["route"]));
  });

  it("activeNodes() returns a snapshot copy, not a live reference", () => {
    const store = createStore();
    store.applyNodeDelta("route", true);
    const snap = store.activeNodes();
    store.applyNodeDelta("draft", true); // mutate after snapshot
    expect(snap).toEqual(new Set(["route"]));
  });
});
