/**
 * M5 — TodoMVC end-to-end: the proof of thesis.
 *
 * Real node:http + real `ws` client + real better-sqlite3 (`:memory:`). State
 * lives 100% in sqlite; the browser holds none. Every assertion follows the
 * same loop: an htmx `ws-send` frame → action → DB write → live regions
 * re-rendered FROM the DB → `hx-swap-oob` frames pushed back. The multi-client
 * test proves the signal hub fans one change out to every connected tab.
 */
import { afterEach, describe, expect, it } from "vitest";
import { createServer } from "node:http";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { WebSocket } from "ws";
import type { RawData } from "ws";
import { streamShell } from "../src/index.js";
import { openTodoDb, listTodos } from "../demo/todomvc/db.js";
import type { DB } from "../demo/todomvc/db.js";
import { createTodoApp } from "../demo/todomvc/app.js";
import type { TodoApp } from "../demo/todomvc/app.js";

interface Harness {
  http: Server;
  db: DB;
  app: TodoApp;
  port: number;
  wsUrl: string;
}

async function start(): Promise<Harness> {
  const db = openTodoDb(":memory:");
  const app = createTodoApp(db);
  const http = createServer((req, res) => {
    if (req.method === "GET" && req.url === "/") {
      void streamShell(res, app.page());
      return;
    }
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("not found");
  });
  http.on("upgrade", (req, socket, head) =>
    app.vws.handleUpgrade(req, socket, head),
  );
  await new Promise<void>((r) => http.listen(0, r));
  const port = (http.address() as AddressInfo).port;
  return { http, db, app, port, wsUrl: `ws://127.0.0.1:${port}/ws` };
}

async function stop(h: Harness): Promise<void> {
  await h.app.vws.close();
  // Destroy any still-open upgraded sockets so http.close() can resolve even
  // if a test bailed before closing its client.
  h.http.closeAllConnections();
  await new Promise<void>((r) => h.http.close(() => r()));
  h.db.close();
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

const sleep = (ms: number): Promise<void> =>
  new Promise((r) => setTimeout(r, ms));

/** An htmx `ws-send` frame: form fields + the HEADERS envelope htmx adds. */
function frame(fields: Record<string, string>): string {
  return JSON.stringify({
    ...fields,
    HEADERS: {
      "HX-Request": "true",
      "HX-Trigger": null,
      "HX-Trigger-Name": fields.action ?? null,
      "HX-Target": null,
      "HX-Current-URL": "http://localhost/",
    },
  });
}

describe("M5 — TodoMVC end-to-end", () => {
  let h: Harness | null = null;
  afterEach(async () => {
    if (h) await stop(h);
    h = null;
  });

  it("serves a shell that wires the WS and mounts the live regions", async () => {
    h = await start();
    const res = await fetch(`http://127.0.0.1:${h.port}/`);
    const body = await res.text();

    expect(res.headers.get("content-type")).toBe("text/html; charset=utf-8");
    // htmx + ws extension drive the page; no app JS, no client state store.
    expect(body).toContain('hx-ext="ws"');
    expect(body).toContain('ws-connect="/ws"');
    expect(body).toContain("htmx.org@2.0.4");
    expect(body).toContain("htmx-ext-ws@2.0.2");
    // The live regions are mounted inline behind their stable ids.
    expect(body).toContain('<vinyl-slot id="vinyl-r-todos">');
    expect(body).toContain('<vinyl-slot id="vinyl-r-count">');
    expect(body).toContain('<ul class="todo-list"></ul>');
  });

  it("an add frame writes sqlite and pushes the todo + count OOB", async () => {
    h = await start();
    const ws = new WebSocket(h.wsUrl);
    await waitOpen(ws);

    const msgP = nextMessage(ws);
    ws.send(frame({ action: "add", text: "buy milk" }));
    const msg = await msgP;

    // State landed in sqlite.
    expect(listTodos(h.db)).toEqual([{ id: 1, text: "buy milk", done: false }]);
    // Both bound regions came back as OOB swaps, rendered from the DB.
    expect(msg).toContain(
      '<vinyl-slot id="vinyl-r-todos" hx-swap-oob="true">',
    );
    expect(msg).toContain('<span class="text">buy milk</span>');
    expect(msg).toContain(
      '<vinyl-slot id="vinyl-r-count" hx-swap-oob="true"><span class="count">1 left</span></vinyl-slot>',
    );
    ws.close();
  });

  it("escapes todo text on the way back out (XSS-safe)", async () => {
    h = await start();
    const ws = new WebSocket(h.wsUrl);
    await waitOpen(ws);

    const msgP = nextMessage(ws);
    ws.send(frame({ action: "add", text: "<img src=x onerror=alert(1)>" }));
    const msg = await msgP;

    expect(listTodos(h.db)[0]?.text).toBe("<img src=x onerror=alert(1)>");
    expect(msg).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(msg).not.toContain("<img src=x");
    ws.close();
  });

  it("toggle marks the todo done and the counter drops to 0 left", async () => {
    h = await start();
    const ws = new WebSocket(h.wsUrl);
    await waitOpen(ws);

    await (async () => {
      const p = nextMessage(ws);
      ws.send(frame({ action: "add", text: "ship it" }));
      await p;
    })();
    const id = listTodos(h.db)[0]?.id ?? 0;

    const togP = nextMessage(ws);
    ws.send(frame({ action: "toggle", id: String(id) }));
    const msg = await togP;

    expect(listTodos(h.db)[0]?.done).toBe(true);
    expect(msg).toContain('<li class="completed">');
    expect(msg).toContain(
      '<span class="count">0 left</span>',
    );
    ws.close();
  });

  it("clear removes completed todos and keeps the active ones", async () => {
    h = await start();
    const ws = new WebSocket(h.wsUrl);
    await waitOpen(ws);

    for (const text of ["a", "b"]) {
      const p = nextMessage(ws);
      ws.send(frame({ action: "add", text }));
      await p;
    }
    const first = listTodos(h.db)[0]?.id ?? 0;
    {
      const p = nextMessage(ws);
      ws.send(frame({ action: "toggle", id: String(first) }));
      await p;
    }

    const clrP = nextMessage(ws);
    ws.send(frame({ action: "clear" }));
    const msg = await clrP;

    expect(listTodos(h.db).map((t) => t.text)).toEqual(["b"]);
    expect(msg).toContain('<span class="text">b</span>');
    expect(msg).not.toContain('<span class="text">a</span>');
    ws.close();
  });

  it("fans one client's change out to every connected client (live)", async () => {
    h = await start();
    const ws1 = new WebSocket(h.wsUrl);
    const ws2 = new WebSocket(h.wsUrl);
    await Promise.all([waitOpen(ws1), waitOpen(ws2)]);
    // Ensure both onConnect subscriptions have registered server-side.
    await sleep(30);
    expect(h.app.ctx.hub.size).toBe(2); // one record per connection

    const p1 = nextMessage(ws1);
    const p2 = nextMessage(ws2);
    ws1.send(frame({ action: "add", text: "shared" }));
    const [m1, m2] = await Promise.all([p1, p2]);

    // The acting client AND the passive client both receive the OOB update.
    expect(m1).toContain('<span class="text">shared</span>');
    expect(m2).toContain('<span class="text">shared</span>');
    ws1.close();
    ws2.close();
  });

  it("drops subscriptions when a connection closes", async () => {
    h = await start();
    const ws = new WebSocket(h.wsUrl);
    await waitOpen(ws);
    await sleep(20);
    expect(h.app.ctx.hub.size).toBe(1); // one record (two regions) per conn

    ws.close();
    await sleep(40);
    expect(h.app.ctx.hub.size).toBe(0);
  });
});
