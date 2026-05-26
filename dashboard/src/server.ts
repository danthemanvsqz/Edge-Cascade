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
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { streamShell } from "@danthemanvsqz/vinyl";

import { createDashboardApp } from "./app.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const STYLE_CSS_PATH = resolve(HERE, "style.css");

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
    void readFile(STYLE_CSS_PATH).then(
      (body) => {
        res.writeHead(200, { "content-type": "text/css; charset=utf-8" });
        res.end(body);
      },
      () => {
        res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
        res.end("style.css missing");
      },
    );
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
