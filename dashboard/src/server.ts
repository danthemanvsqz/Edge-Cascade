/**
 * Runtime entry. `node --import tsx src/server.ts` (or `npm run dev`).
 *
 * Mirrors the structure of projects/vinyl/demo/todomvc/server.ts: node:http
 * for the initial paint via `streamShell`; the `upgrade` event hands off to
 * Vinyl's WS server for the live-region push fabric. The tailer is started
 * before listen so the dashboard is already warm by the time a browser hits
 * the page.
 *
 * Env knobs:
 *   PORT       -- TCP port (default 8789; deliberately not 8788 so the
 *                 TodoMVC demo can run alongside)
 *   RUNS_DIR   -- where to tail .rec files from (default ../runs, resolved
 *                 from the dashboard package root so a fresh clone "just
 *                 works" when started from `npm run dev`)
 */
import { createServer } from "node:http";
import { resolve } from "node:path";
import { streamShell } from "@danthemanvsqz/vinyl";

import { createDashboardApp } from "./app.js";

const port = Number(process.env.PORT ?? 8789);
const runsDir = resolve(process.env.RUNS_DIR ?? "../runs");

const app = createDashboardApp({ runsDir });
app.tailer.start();

const server = createServer((req, res) => {
  if (req.method === "GET" && (req.url === "/" || req.url === "/index.html")) {
    void streamShell(res, app.page());
    return;
  }
  if (req.method === "GET" && req.url === "/style.css") {
    // Slice 6 will replace this with a real palette + keyframes. For now we
    // serve an empty file so the browser doesn't log a 404 in the demo.
    res.writeHead(200, { "content-type": "text/css; charset=utf-8" });
    res.end("");
    return;
  }
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
  res.end("not found");
});

server.on("upgrade", (req, socket, head) => {
  app.vws.handleUpgrade(req, socket, head);
});

server.listen(port, () => {
  console.log(
    `edge-cascade dashboard on http://localhost:${String(port)} (tailing ${runsDir})`,
  );
});
