/**
 * TodoMVC demo — runnable entry. `node --import tsx demo/todomvc/server.ts`
 * (or build first). Open http://localhost:8788 in two tabs and watch them stay
 * in sync: every change is a server round-trip pushed over the WebSocket, with
 * the browser holding zero state.
 *
 * Set TODO_DB to a file path to persist across restarts (default: in-memory).
 */
import { createServer } from "node:http";
import { streamShell } from "../../src/index.js";
import { openTodoDb } from "./db.js";
import { createTodoApp } from "./app.js";

const db = openTodoDb(process.env.TODO_DB ?? ":memory:");
const app = createTodoApp(db);
const port = Number(process.env.PORT ?? 8788);

const server = createServer((req, res) => {
  if (req.method === "GET" && (req.url === "/" || req.url === "/index.html")) {
    void streamShell(res, app.page());
    return;
  }
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
  res.end("not found");
});

server.on("upgrade", (req, socket, head) =>
  app.vws.handleUpgrade(req, socket, head),
);

server.listen(port, () => {
  console.log(`Vinyl · TodoMVC on http://localhost:${String(port)}`);
});
