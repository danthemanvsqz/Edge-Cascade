/**
 * M5 — inbound actions: message parsing + the dispatch router.
 *
 * The router is exercised with a fake connection (only context + push matter);
 * the real WS round-trip is proven in the TodoMVC integration test.
 */
import { describe, expect, it, vi } from "vitest";
import {
  parseMessage,
  defineAction,
  createActionRouter,
} from "../src/actions.js";
import { liveRegion } from "../src/live.js";
import { h } from "../src/vnode.js";
import type { VinylConnection } from "../src/ws.js";

interface Store {
  items: string[];
}

function fakeConn<C>(context: C): { conn: VinylConnection<C>; sent: string[] } {
  const sent: string[] = [];
  const conn = {
    context,
    push(...elements: string[]) {
      if (elements.length === 0) return;
      sent.push(elements.join(""));
    },
    send() {},
    close() {},
  } as unknown as VinylConnection<C>;
  return { conn, sent };
}

describe("M5 — parseMessage", () => {
  it("splits HEADERS metadata from form input", () => {
    const msg = parseMessage(
      JSON.stringify({
        text: "milk",
        action: "add",
        HEADERS: {
          "HX-Request": "true",
          "HX-Trigger": "add-form",
          "HX-Trigger-Name": "add",
        },
      }),
    );
    expect(msg.input).toEqual({ text: "milk", action: "add" });
    expect(msg.headers).toEqual({
      "HX-Request": "true",
      "HX-Trigger": "add-form",
      "HX-Trigger-Name": "add",
    });
  });

  it("coerces non-string header values (htmx sends null) to null", () => {
    const msg = parseMessage(
      JSON.stringify({ HEADERS: { "HX-Trigger": null, "HX-Target": null } }),
    );
    expect(msg.headers).toEqual({ "HX-Trigger": null, "HX-Target": null });
    expect(msg.input).toEqual({});
  });

  it("handles a non-object frame (empty input/headers, raw preserved)", () => {
    const msg = parseMessage("42");
    expect(msg.raw).toBe(42);
    expect(msg.input).toEqual({});
    expect(msg.headers).toEqual({});
  });

  it("throws on malformed JSON (the router catches it)", () => {
    expect(() => parseMessage("{not json")).toThrow();
  });
});

describe("M5 — createActionRouter", () => {
  const itemsRegion = liveRegion<Store>("items", (ctx) =>
    h("ul", null, ...ctx.items.map((t) => h("li", null, t))),
  );

  function makeRouter() {
    const add = defineAction<Store>("add", (ctx) => {
      ctx.context.items.push(String(ctx.input.text));
      ctx.refresh(itemsRegion);
    });
    return createActionRouter<Store>({ actions: [add] });
  }

  it("dispatches by input.action, runs the handler, and pushes the frame", async () => {
    const route = makeRouter();
    const { conn, sent } = fakeConn<Store>({ items: [] });
    await route(conn, JSON.stringify({ action: "add", text: "milk" }));

    expect(conn.context.items).toEqual(["milk"]);
    expect(sent).toEqual([itemsRegion.frame(conn.context)]);
    expect(sent[0]).toContain("<li>milk</li>");
  });

  it("falls back to HX-Trigger-Name when input.action is absent", async () => {
    const route = makeRouter();
    const { conn } = fakeConn<Store>({ items: [] });
    await route(
      conn,
      JSON.stringify({
        text: "eggs",
        HEADERS: { "HX-Trigger-Name": "add" },
      }),
    );
    expect(conn.context.items).toEqual(["eggs"]);
  });

  it("routes unknown actions to onUnknown without throwing", async () => {
    const onUnknown = vi.fn();
    const route = createActionRouter<Store>({ actions: [], onUnknown });
    const { conn } = fakeConn<Store>({ items: [] });
    await route(conn, JSON.stringify({ action: "nope" }));
    expect(onUnknown).toHaveBeenCalledWith("nope", conn);
  });

  it("treats a frame with no resolvable name as unknown", async () => {
    const onUnknown = vi.fn();
    const route = createActionRouter<Store>({ actions: [], onUnknown });
    const { conn } = fakeConn<Store>({ items: [] });
    await route(conn, JSON.stringify({ text: "x" }));
    expect(onUnknown).toHaveBeenCalledWith(undefined, conn);
  });

  it("routes malformed JSON to onParseError without throwing", async () => {
    const onParseError = vi.fn();
    const route = createActionRouter<Store>({ actions: [], onParseError });
    const { conn } = fakeConn<Store>({ items: [] });
    await route(conn, "{not json");
    expect(onParseError).toHaveBeenCalledOnce();
    expect(onParseError.mock.calls[0]?.[0]).toBe("{not json");
  });

  it("routes a throwing handler to onError and keeps the socket alive", async () => {
    const onError = vi.fn();
    const boom = defineAction<Store>("boom", () => {
      throw new Error("kaboom");
    });
    const route = createActionRouter<Store>({ actions: [boom], onError });
    const { conn } = fakeConn<Store>({ items: [] });
    await expect(
      route(conn, JSON.stringify({ action: "boom" })),
    ).resolves.toBeUndefined();
    expect(onError).toHaveBeenCalledOnce();
    expect(onError.mock.calls[0]?.[1]).toBe("boom");
  });

  it("awaits async handlers before resolving", async () => {
    const seen: string[] = [];
    const slow = defineAction<Store>("slow", async (ctx) => {
      await Promise.resolve();
      ctx.context.items.push("done");
      seen.push("ran");
    });
    const route = createActionRouter<Store>({ actions: [slow] });
    const { conn } = fakeConn<Store>({ items: [] });
    await route(conn, JSON.stringify({ action: "slow" }));
    expect(seen).toEqual(["ran"]);
    expect(conn.context.items).toEqual(["done"]);
  });

  it("supports a custom nameFrom resolver", async () => {
    const ran = vi.fn();
    const op = defineAction<Store>("op", () => ran());
    const route = createActionRouter<Store>({
      actions: [op],
      nameFrom: (msg) =>
        typeof msg.input.cmd === "string" ? msg.input.cmd : undefined,
    });
    const { conn } = fakeConn<Store>({ items: [] });
    await route(conn, JSON.stringify({ cmd: "op" }));
    expect(ran).toHaveBeenCalledOnce();
  });
});
