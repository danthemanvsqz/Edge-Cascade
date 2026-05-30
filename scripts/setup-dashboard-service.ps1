# setup-dashboard-service.ps1
# One-time setup: register the edge-cascade dashboard as a PM2-managed process
# that starts automatically on Windows boot and restarts on crash.
#
# Run once (no elevation required for user-scope task scheduler):
#   powershell -ExecutionPolicy Bypass -File scripts\setup-dashboard-service.ps1
#
# After setup:
#   pm2 status                         -- see uptime, restart count, CPU/mem
#   pm2 logs edge-dashboard            -- tail live logs (access log + stdout)
#   pm2 reload edge-dashboard          -- zero-downtime redeploy after a code change
#   pm2 stop edge-dashboard            -- take it down deliberately

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot  = Split-Path $PSScriptRoot -Parent
$DashboardDir = Join-Path $ProjectRoot "dashboard"

# ── Ensure PM2 is installed ───────────────────────────────────────────────────
if (-not (Get-Command pm2 -ErrorAction SilentlyContinue)) {
    Write-Host "PM2 not found. Installing globally..."
    npm install -g pm2
    if ($LASTEXITCODE -ne 0) { throw "npm install -g pm2 failed" }
}

$pm2Version = (pm2 --version 2>&1)
Write-Host "PM2 $pm2Version"

# ── Start / update the dashboard process ─────────────────────────────────────
Push-Location $DashboardDir
try {
    # `pm2 start` is idempotent when the name already exists: it's a no-op if
    # already running, so safe to re-run this script at any time.
    pm2 start ecosystem.config.cjs --update-env
    if ($LASTEXITCODE -ne 0) { throw "pm2 start failed" }

    # Persist the process list so PM2 resurrects it after a pm2 restart/reboot.
    pm2 save
    if ($LASTEXITCODE -ne 0) { throw "pm2 save failed" }
} finally {
    Pop-Location
}

# ── Windows auto-start (Task Scheduler, user scope) ──────────────────────────
# PM2 on Windows registers a Task Scheduler entry that runs `pm2 resurrect` at
# logon. This brings up all saved PM2 processes without needing elevation.
$pm2Bin = (Get-Command pm2).Source
$taskName = "PM2-EdgeCascade-Dashboard"
$action   = New-ScheduledTaskAction -Execute "node" `
              -Argument "`"$(pm2 --no-color startup | Select-String 'pm2 resurrect' | ForEach-Object { $_.Line.Trim() })`""

# Simpler fallback: just run `pm2 resurrect` at logon
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$action   = New-ScheduledTaskAction -Execute $pm2Bin -Argument "resurrect"

try {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Limited -Description "Resurrect PM2 processes (edge-cascade dashboard) at logon" | Out-Null
    Write-Host "Task Scheduler entry '$taskName' registered (runs at logon)."
} catch {
    Write-Warning "Could not register Task Scheduler entry: $_"
    Write-Host "Manual alternative: add 'pm2 resurrect' to your Windows startup folder."
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
pm2 status
Write-Host ""
Write-Host "Setup complete. The dashboard will:"
Write-Host "  - Start automatically when you log in to Windows"
Write-Host "  - Restart within 2s if it crashes"
Write-Host "  - Log access events to runs/dashboard-access.log"
Write-Host "  - Log stdout/stderr to runs/dashboard-pm2-out.log"
Write-Host ""
Write-Host "Stability check:"
Write-Host "  pm2 status                 # uptime + restart count"
Write-Host "  tail runs/dashboard-access.log  # session starts + requests"
