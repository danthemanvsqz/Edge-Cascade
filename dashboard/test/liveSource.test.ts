import { describe, expect, it } from "vitest";
import type { Redis } from "ioredis";

import {
  createLiveSource,
  LIVE_CHANNEL,
  LIVE_STATE_KEY,
  parseDelta,
  parseSeed,
} from "../src/lib/liveSource.js";
import { createStore } from "../src/store.js";

describe("parseDelta", () => {
  it("parses an active delta", () => {
    expect(parseDelta(JSON.stringify({ node: "gpu_solve", state: "active" }))).toEqual({
      node: "gpu_solve",
      active: true,
    });
  });

  it("treats any non-active state as idle", () => {
    expect(parseDelta(JSON.stringify({ node: "route", state: "idle" }))).toEqual({
      node: "route",
      active: false,
    });
  });

  it("returns null on malformed JSON, non-object, or missing/non-string node", () => {
    expect(parseDelta("not json {")).toBeNull();
    expect(parseDelta("123")).toBeNull();
    expect(parseDelta(JSON.stringify({ state: "active" }))).toBeNull();
    expect(parseDelta(JSON.stringify({ node: 5, state: "active" }))).toBeNull();
  });
});

describe("parseSeed", () => {
  it("returns the string elements of a JSON array", () => {
    expect(parseSeed(JSON.stringify(["route", "gpu_solve"]))).toEqual(["route", "gpu_solve"]);
  });

  it("drops non-string elements", () => {
    expect(parseSeed(JSON.stringify(["route", 5, null, "draft"]))).toEqual(["route", "draft"]);
  });

  it("returns [] for null, malformed JSON, or a non-array", () => {
    expect(parseSeed(null)).toEqual([]);
    expect(parseSeed("not json [")).toEqual([]);
    expect(parseSeed(JSON.stringify({ node: "x" }))).toEqual([]);
  });
});

/** Minimal in-memory redis double: GET from a map, capture the message handler,
 * and let the test push messages via `emit`. */
class FakeRedis {
  readonly data: Record<string, string> = {};
  private handler: ((channel: string, message: string) => void) | null = null;

  get(key: string): Promise<string | null> {
    return Promise.resolve(this.data[key] ?? null);
  }

  on(event: string, cb: (channel: string, message: string) => void): this {
    if (event === "message") this.handler = cb;
    return this;
  }

  subscribe(_channel: string): Promise<number> {
    return Promise.resolve(1);
  }

  quit(): Promise<"OK"> {
    return Promise.resolve("OK");
  }

  emit(channel: string, message: string): void {
    this.handler?.(channel, message);
  }
}

describe("createLiveSource", () => {
  it("seeds active nodes on start, then applies deltas, calling onChange each time", async () => {
    const fake = new FakeRedis();
    fake.data[LIVE_STATE_KEY] = JSON.stringify(["gpu_solve"]);
    const store = createStore();
    let changes = 0;
    const src = createLiveSource({
      store,
      onChange: () => {
        changes += 1;
      },
      createClient: () => fake as unknown as Redis,
    });

    await src.start();
    expect(store.activeNodes()).toEqual(new Set(["gpu_solve"])); // seeded
    expect(changes).toBe(1);

    fake.emit(LIVE_CHANNEL, JSON.stringify({ node: "route", state: "active" }));
    expect(store.activeNodes()).toEqual(new Set(["gpu_solve", "route"]));
    expect(changes).toBe(2);

    fake.emit(LIVE_CHANNEL, JSON.stringify({ node: "gpu_solve", state: "idle" }));
    expect(store.activeNodes()).toEqual(new Set(["route"]));
    expect(changes).toBe(3);

    await src.stop();
  });

  it("skips seeding when the key is absent and ignores malformed deltas", async () => {
    const fake = new FakeRedis(); // no seed key set
    const store = createStore();
    let changes = 0;
    const src = createLiveSource({
      store,
      onChange: () => {
        changes += 1;
      },
      createClient: () => fake as unknown as Redis,
    });

    await src.start();
    expect(store.activeNodes()).toEqual(new Set()); // nothing seeded
    expect(changes).toBe(0);

    fake.emit(LIVE_CHANNEL, "garbage {"); // malformed -> ignored
    expect(store.activeNodes()).toEqual(new Set());
    expect(changes).toBe(0);

    await src.stop();
  });
});
