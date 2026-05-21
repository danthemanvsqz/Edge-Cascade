/**
 * M5 — live regions + signal hub (the render half of the state model).
 *
 * Regions are tested directly (id / inline mount / OOB frame) and the hub is
 * driven with fake connections so the per-connection-context re-render is
 * observable without standing up sockets. The real WS round-trip lives in the
 * TodoMVC integration test.
 */
import { describe, expect, it } from "vitest";
import { liveRegion, regionId, createSignalHub } from "../src/live.js";
import { renderToString } from "../src/render.js";
import { h } from "../src/vnode.js";
import type { VinylConnection } from "../src/ws.js";

interface Store {
  items: string[];
}

/** Minimal VinylConnection stand-in: only context + push() are exercised. */
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

const itemsRegion = liveRegion<Store>("items", (ctx) =>
  h("ul", null, ...ctx.items.map((t) => h("li", null, t))),
);

describe("M5 — regionId", () => {
  it("prefixes with vinyl-r- and slugifies the key", () => {
    expect(regionId("items")).toBe("vinyl-r-items");
    expect(regionId("Todo List!")).toBe("vinyl-r-todo-list");
  });

  it("never collides with the Suspense id namespace (vinyl-s-)", () => {
    expect(regionId("0")).toBe("vinyl-r-0");
    expect(regionId("0").startsWith("vinyl-s-")).toBe(false);
  });
});

describe("M5 — liveRegion", () => {
  it("mount() renders the content inline behind a plain <vinyl-slot id>", () => {
    const html = renderToString(itemsRegion.mount({ items: ["a", "b"] }));
    expect(html).toBe(
      '<vinyl-slot id="vinyl-r-items"><ul><li>a</li><li>b</li></ul></vinyl-slot>',
    );
  });

  it("frame() renders the same content as an OOB swap for the same id", () => {
    const frame = itemsRegion.frame({ items: ["a", "b"] });
    expect(frame).toBe(
      '<vinyl-slot id="vinyl-r-items" hx-swap-oob="true">' +
        "<ul><li>a</li><li>b</li></ul></vinyl-slot>",
    );
  });

  it("re-renders from current context each call (no cached state)", () => {
    const store: Store = { items: [] };
    expect(itemsRegion.frame(store)).toContain("<ul></ul>");
    store.items.push("x");
    expect(itemsRegion.frame(store)).toContain("<ul><li>x</li></ul>");
  });

  it("escapes string content (XSS-safe like the renderer)", () => {
    const r = liveRegion<{ name: string }>("greet", (ctx) => ctx.name);
    expect(r.frame({ name: "<script>" })).toBe(
      '<vinyl-slot id="vinyl-r-greet" hx-swap-oob="true">&lt;script&gt;</vinyl-slot>',
    );
  });
});

describe("M5 — createSignalHub", () => {
  it("emit() pushes each subscriber its OWN context-rendered frame", () => {
    const hub = createSignalHub<Store>();
    const a = fakeConn<Store>({ items: ["a"] });
    const b = fakeConn<Store>({ items: ["b1", "b2"] });
    hub.subscribe("items", a.conn, itemsRegion);
    hub.subscribe("items", b.conn, itemsRegion);
    expect(hub.size).toBe(2);

    hub.emit("items");
    expect(a.sent).toEqual([itemsRegion.frame({ items: ["a"] })]);
    expect(b.sent).toEqual([itemsRegion.frame({ items: ["b1", "b2"] })]);
  });

  it("coalesces a subscriber's multiple regions into one frame", () => {
    const hub = createSignalHub<Store>();
    const count = liveRegion<Store>("count", (ctx) => String(ctx.items.length));
    const a = fakeConn<Store>({ items: ["x", "y"] });
    hub.subscribe("items", a.conn, itemsRegion, count);

    hub.emit("items");
    expect(a.sent).toEqual([
      itemsRegion.frame(a.conn.context) + count.frame(a.conn.context),
    ]);
  });

  it("unsubscribe() stops delivery and shrinks size", () => {
    const hub = createSignalHub<Store>();
    const a = fakeConn<Store>({ items: ["a"] });
    const off = hub.subscribe("items", a.conn, itemsRegion);
    expect(hub.size).toBe(1);

    off();
    expect(hub.size).toBe(0);
    hub.emit("items");
    expect(a.sent).toEqual([]);
  });

  it("emit() on an unknown key is a no-op", () => {
    const hub = createSignalHub<Store>();
    const a = fakeConn<Store>({ items: ["a"] });
    hub.subscribe("items", a.conn, itemsRegion);
    hub.emit("nope");
    expect(a.sent).toEqual([]);
  });

  it("remove(conn) drops all of a connection's subscriptions across keys", () => {
    const hub = createSignalHub<Store>();
    const a = fakeConn<Store>({ items: ["a"] });
    const b = fakeConn<Store>({ items: ["b"] });
    hub.subscribe("items", a.conn, itemsRegion);
    hub.subscribe("other", a.conn, itemsRegion);
    hub.subscribe("items", b.conn, itemsRegion);
    expect(hub.size).toBe(3);

    hub.remove(a.conn);
    expect(hub.size).toBe(1);
    hub.emit("items");
    expect(a.sent).toEqual([]);
    expect(b.sent).toEqual([itemsRegion.frame({ items: ["b"] })]);
  });
});
