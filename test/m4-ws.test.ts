/**
 * M4 — WS transport round-trip. Real node:http + real `ws` client.
 *
 * The library is bring-your-own-router; `createWSServer` only owns the
 * socket lifecycle, the per-conn context, and `push()`. These tests cover
 * the contract surface: handshake, per-conn context, push framing, message
 * fan-in, lifecycle hooks, and upgrade rejection.
 *
 * NOTE: the server pushes inside `onConnect`, which runs in the same tick
 * that produced the 101 handshake — the client's `message` event can fire
 * immediately after `open`. Tests attach message handlers BEFORE awaiting
 * open so the listener is in place when frames arrive.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import type { IncomingMessage, Server } from "node:http";
import { WebSocket } from "ws";
import type { RawData } from "ws";
import type { VinylConnection, VinylWSServer } from "../src/ws.js";
import { createWSServer } from "../src/ws.js";
import { oob } from "../src/oob.js";
import { streamShell } from "../src/shell.js";
import { h } from "../src/vnode.js";

interface Harness<C> {
  http: Server;
  vws: VinylWSServer;
  port: number;
  url: string;
  conns: VinylConnection<C>[];
  closed: VinylConnection<C>[];
  inbound: Array<{ conn: VinylConnection<C>; data: string }>;
}

async function listen<C>(
  ctxFactory: (req: IncomingMessage) => C | Promise<C>,
  hooks: {
    onConnect?(conn: VinylConnection<C>): void | Promise<void>;
    onMessage?(conn: VinylConnection<C>, data: string): void | Promise<void>;
    path?: string;
  } = {},
): Promise<Harness<C>> {
  const conns: VinylConnection<C>[] = [];
  const closed: VinylConnection<C>[] = [];
  const inbound: Array<{ conn: VinylConnection<C>; data: string }> = [];

  const vws = createWSServer<C>({
    context: ctxFactory,
    path: hooks.path,
    onConnect(conn) {
      conns.push(conn);
      return hooks.onConnect?.(conn);
    },
    onMessage(conn, data) {
      inbound.push({ conn, data });
      return hooks.onMessage?.(conn, data);
    },
    onClose(conn) {
      closed.push(conn);
    },
  });

  const http = createServer();
  http.on("upgrade", (req, socket, head) =>
    vws.handleUpgrade(req, socket, head),
  );
  await new Promise<void>((r) => http.listen(0, r));
  const port = (http.address() as AddressInfo).port;
  return {
    http,
    vws,
    port,
    url: `ws://127.0.0.1:${port}`,
    conns,
    closed,
    inbound,
  };
}

async function stop<C>(h: Harness<C>): Promise<void> {
  await h.vws.close();
  await new Promise<void>((r) => h.http.close(() => r()));
}

function decode(data: RawData, isBinary: boolean): string {
  if (isBinary) return "";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return Buffer.concat(data).toString("utf8");
  if (Buffer.isBuffer(data)) return data.toString("utf8");
  return Buffer.from(data).toString("utf8");
}

function nextMessage(ws: WebSocket): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    ws.once("message", (data: RawData, isBinary: boolean) =>
      resolve(decode(data, isBinary)),
    );
    ws.once("error", reject);
    ws.once("close", () => reject(new Error("closed before message")));
  });
}

function waitOpen(ws: WebSocket): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    ws.once("open", () => resolve());
    ws.once("error", reject);
  });
}

function waitClose(ws: WebSocket): Promise<{ code: number; reason: string }> {
  return new Promise<{ code: number; reason: string }>((resolve) => {
    // Reject paths produce an "error" + "close" pair — swallow the error so
    // it isn't an unhandled exception; the close payload is what we want.
    ws.on("error", () => {});
    ws.once("close", (code, reason) =>
      resolve({ code, reason: reason.toString("utf8") }),
    );
  });
}

const sleep = (ms: number): Promise<void> =>
  new Promise((r) => setTimeout(r, ms));

describe("M4 — WS transport", () => {
  let h: Harness<{ user: string }> | null = null;

  beforeEach(() => {
    h = null;
  });
  afterEach(async () => {
    if (h) await stop(h);
  });

  it("accepts an upgrade and round-trips a pushed OOB frame", async () => {
    h = await listen<{ user: string }>(() => ({ user: "alice" }), {
      onConnect(conn) {
        conn.push(oob("vinyl-s-0", "<p>hi</p>"));
      },
    });

    const ws = new WebSocket(h.url);
    const msgP = nextMessage(ws);
    await waitOpen(ws);
    expect(await msgP).toBe(
      '<vinyl-slot id="vinyl-s-0" hx-swap-oob="true"><p>hi</p></vinyl-slot>',
    );
    ws.close();
  });

  it("push() concatenates multiple OOB elements into a single message", async () => {
    h = await listen<{ user: string }>(() => ({ user: "bob" }), {
      onConnect(conn) {
        conn.push(oob("a", "<i>1</i>"), oob("b", "<i>2</i>"));
      },
    });

    const ws = new WebSocket(h.url);
    const msgP = nextMessage(ws);
    await waitOpen(ws);
    expect(await msgP).toBe(
      '<vinyl-slot id="a" hx-swap-oob="true"><i>1</i></vinyl-slot>' +
        '<vinyl-slot id="b" hx-swap-oob="true"><i>2</i></vinyl-slot>',
    );
    ws.close();
  });

  it("push() with no elements is a no-op (does not send an empty frame)", async () => {
    let pushed = 0;
    h = await listen<{ user: string }>(() => ({ user: "x" }), {
      onConnect(conn) {
        conn.push();
        conn.push("a");
        pushed = 2;
      },
    });

    const ws = new WebSocket(h.url);
    const received: string[] = [];
    ws.on("message", (data, isBinary) => received.push(decode(data, isBinary)));
    await waitOpen(ws);
    await sleep(30);
    expect(pushed).toBe(2);
    expect(received).toEqual(["a"]);
    ws.close();
  });

  it("per-conn context flows from the factory to the connection", async () => {
    h = await listen<{ user: string }>((req) => ({
      user: req.headers["x-user"] === "alice" ? "alice" : "anon",
    }));

    const ws = new WebSocket(h.url, { headers: { "x-user": "alice" } });
    await waitOpen(ws);
    expect(h.conns).toHaveLength(1);
    expect(h.conns[0]?.context).toEqual({ user: "alice" });
    ws.close();
  });

  it("inbound text messages reach onMessage with utf-8 decoding", async () => {
    const got: string[] = [];
    h = await listen<{ user: string }>(() => ({ user: "carol" }), {
      onMessage(_conn, data) {
        got.push(data);
      },
    });

    const ws = new WebSocket(h.url);
    await waitOpen(ws);
    ws.send("hello — résumé");
    await sleep(30);
    expect(got).toEqual(["hello — résumé"]);
    ws.close();
  });

  it("rejects an upgrade with 401 when context() throws", async () => {
    h = await listen<{ user: string }>(() => {
      throw new Error("unauthorized");
    });

    const ws = new WebSocket(h.url);
    ws.on("error", () => {});
    await waitClose(ws);
    expect(h.conns).toHaveLength(0);
  });

  it("rejects with 404 when path is set and pathname differs", async () => {
    h = await listen<{ user: string }>(() => ({ user: "x" }), {
      path: "/ws",
    });

    const ws = new WebSocket(h.url + "/wrong");
    ws.on("error", () => {});
    await waitClose(ws);
    expect(h.conns).toHaveLength(0);
  });

  it("accepts when path matches", async () => {
    h = await listen<{ user: string }>(() => ({ user: "x" }), {
      path: "/ws",
    });

    const ws = new WebSocket(h.url + "/ws");
    await waitOpen(ws);
    expect(h.conns).toHaveLength(1);
    ws.close();
  });

  it("onClose fires once per closed connection", async () => {
    h = await listen<{ user: string }>(() => ({ user: "x" }));

    const ws = new WebSocket(h.url);
    await waitOpen(ws);
    ws.close();
    await sleep(30);
    expect(h.closed).toHaveLength(1);
  });

  it("conn.close(code, reason) closes with the supplied code/reason", async () => {
    h = await listen<{ user: string }>(() => ({ user: "x" }), {
      onConnect(conn) {
        conn.close(4321, "bye");
      },
    });

    const ws = new WebSocket(h.url);
    const closeP = waitClose(ws);
    await waitOpen(ws);
    const { code, reason } = await closeP;
    expect(code).toBe(4321);
    expect(reason).toBe("bye");
  });
});

describe("M4 — streamShell HTTP handoff", () => {
  it("streams a vinyl tree to a node:http response as text/html", async () => {
    const http = createServer((_req, res) => {
      const tree = h(
        "html",
        null,
        h(
          "body",
          { "hx-ext": "ws", "ws-connect": "/ws" },
          h("h1", null, "Vinyl"),
        ),
      );
      void streamShell(res, tree);
    });
    await new Promise<void>((r) => http.listen(0, r));
    const port = (http.address() as AddressInfo).port;
    try {
      const r = await fetch(`http://127.0.0.1:${port}/`);
      const body = await r.text();
      expect(r.headers.get("content-type")).toBe("text/html; charset=utf-8");
      expect(body).toBe(
        '<html><body hx-ext="ws" ws-connect="/ws"><h1>Vinyl</h1></body></html>',
      );
    } finally {
      await new Promise<void>((r) => http.close(() => r()));
    }
  });
});
