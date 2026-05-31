// PM2 process config for the edge-cascade dashboard server.
// Usage:
//   pm2 start ecosystem.config.cjs        -- start/restart
//   pm2 reload edge-dashboard             -- zero-downtime redeploy (code change)
//   pm2 stop edge-dashboard               -- stop
//   pm2 logs edge-dashboard               -- tail live logs
//   pm2 status                            -- uptime, restarts, CPU/mem
//
// One-time setup (auto-start on Windows boot + crash recovery):
//   cd scripts && powershell -ExecutionPolicy Bypass -File setup-dashboard-service.ps1
const path = require("path");

module.exports = {
  apps: [
    {
      name: "edge-dashboard",
      script: path.join(__dirname, "../node_modules/tsx/dist/cli.mjs"),
      args: "src/server.ts",
      cwd: __dirname,
      env: {
        PORT: "8789",
        START_FROM_EOF: "1",
        // RUNS_DIR defaults to ../runs -- set here to override
      },
      // Never auto-watch: topology updates come from Flower, not file changes.
      watch: false,
      // Restart immediately on crash; PM2 backs off after max_restarts in a
      // short window so a boot-loop doesn't spin forever.
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",     // must stay up ≥10s to count as a clean start
      restart_delay: 2000,   // 2s between restart attempts
      // Log to runs/ alongside the cascade .rec files so one directory holds
      // all session evidence. PM2 appends; old lines = history.
      error_file: path.join(__dirname, "../runs/dashboard-pm2-error.log"),
      out_file:   path.join(__dirname, "../runs/dashboard-pm2-out.log"),
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
  ],
};
