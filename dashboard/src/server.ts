/**
 * Runtime entry. `node --import tsx src/server.ts` (or `npm run dev`).
 *
 * In production, run via PM2 (`pm2 start ecosystem.config.cjs`) so the server
 * auto-starts on Windows boot and recovers from crashes. See
 * scripts/setup-dashboard-service.ps1 for one-time setup.
 *
 * Env knobs:
 *   PORT             -- TCP port (default 8789)
 *   RUNS_DIR         -- where to tail .rec files from (default ../runs)
 *   START_FROM_EOF   -- session-coupling flag (see below)
 *   FLOWER_URL       -- Flower API base URL (default http://127.0.0.1:5555)
 */
import { appendFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { streamShell } from "@danthemanvsqz/vinyl";

import { createDashboardApp } from "./app.js";
import { setTopology } from "./flow.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const STYLE_CSS_PATH = resolve(HERE, "style.css");

const port = Number(process.env.PORT ?? 8789);
const runsDir = resolve(process.env.RUNS_DIR ?? "../runs");
const startFromEof = ["1", "true", "yes"].includes(
  (process.env.START_FROM_EOF ?? "").toLowerCase(),
);

// ── Access log ────────────────────────────────────────────────────────────────
// One log file for the whole runs/ directory. Each line is logfmt: ts= event= ...
// Gaps in timestamps = server was down. Use this to gauge stability.
const ACCESS_LOG = resolve(runsDir, "dashboard-access.log");

function log(fields: Record<string, string | number>): void {
  const line = Object.entries({ ts: new Date().toISOString(), ...fields })
    .map(([k, v]) => {
      const s = String(v);
      return s.includes(" ") ? `${k}="${s}"` : `${k}=${s}`;
    })
    .join(" ");
  try { appendFileSync(ACCESS_LOG, line + "\n"); } catch { /* non-fatal */ }
}

log({ event: "startup", pid: process.pid, port });

// ── App ───────────────────────────────────────────────────────────────────────
const app = createDashboardApp({ runsDir, startFromEof });
app.tailer.start();

// Topology: query Flower for registered tasks; fall back to full CHAIN_SPECS.
void (async () => {
  const FLOWER = process.env.FLOWER_URL ?? "http://127.0.0.1:5555";
  try {
    const res = await fetch(`${FLOWER}/api/workers?refresh=true&status=true`);
    if (res.ok) {
      const workers = (await res.json()) as Record<string, { registered_tasks?: string[] }>;
      const tasks = new Set<string>(
        Object.values(workers).flatMap(w => w.registered_tasks ?? []),
      );
      if (tasks.size > 0) setTopology(tasks);
    }
  } catch {
    // Flower unavailable -- show full topology.
  }
})();

void app.liveSource.start().catch((err: unknown) => {
  console.warn(`[dashboard] live source not started: ${String(err)}`);
  log({ event: "live_source_error", error: String(err) });
});

// ── HTTP server ───────────────────────────────────────────────────────────────
const server = createServer((req, res) => {
  const start = Date.now();
  const method = req.method ?? "GET";
  const path   = req.url ?? "/";

  const done = (status: number) => {
    log({ event: "request", method, path, status, ms: Date.now() - start });
  };

  if (method === "GET" && (path === "/" || path === "/index.html")) {
    res.on("finish", () => done(res.statusCode));
    void streamShell(res, app.page());
    return;
  }
  if (method === "GET" && path === "/style.css") {
    void readFile(STYLE_CSS_PATH).then(
      (body) => {
        res.writeHead(200, { "content-type": "text/css; charset=utf-8" });
        res.end(body);
        done(200);
      },
      () => {
        res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
        res.end("style.css missing");
        done(500);
      },
    );
    return;
  }
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
  res.end("not found");
  done(404);
});

server.on("upgrade", (req, socket, head) => {
  log({ event: "ws_connect", path: req.url ?? "/" });
  app.vws.handleUpgrade(req, socket, head);
});

// Graceful shutdown: drain in-flight requests before PM2 restarts/reloads.
process.on("SIGTERM", () => {
  log({ event: "shutdown", signal: "SIGTERM" });
  server.close(() => process.exit(0));
});

server.listen(port, () => {
  const mode = startFromEof ? " [session-coupled]" : "";
  console.log(
    `edge-cascade dashboard on http://localhost:${String(port)}${mode} (tailing ${runsDir})`,
  );
});
