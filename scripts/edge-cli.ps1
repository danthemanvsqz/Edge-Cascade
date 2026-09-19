<#
.SYNOPSIS
  Stand up the Claude CLI as Tier 3 of the edge cascade, wired to the LOCAL
  inference mesh only (Tier 1 NPU + Tier 2 GPU + the deterministic verifier).

.DESCRIPTION
  0. Supervisor (EDGE-1): probes every Canvas-pipeline dependency in order --
     Docker engine, Redis broker, Ollama, Celery worker (npu,gpu,verify),
     dashboard -- starts whatever is down, prints an UP / RESTARTED / FAILED
     table, and refuses to launch if a critical one (docker, redis, worker)
     stays down (-Force overrides, -NoSupervise skips). Decisions live in
     cascade/health.py; this script only spawns processes.
  1. Ensures the edge-cascade venv has the `accel` + `mcp` + `celery` extras.
  2. Generates a robust, machine-correct MCP config (absolute interpreter
     path + explicit cwd/PYTHONPATH) for the local servers.
  3. Launches the bundled Claude Code CLI with `--mcp-config <that>
     --strict-mcp-config`, so the session sees EXACTLY these servers and
     ignores every other MCP config.

  Tier 4 (`edge-cloud`, the paid Anthropic API) is deliberately NOT wired in:
  with --strict-mcp-config the launched session is structurally incapable of
  spending metered dollars. Pass -WithCloud to opt in explicitly.

.PARAMETER ProjectDir
  Directory to build in (the CLI's working dir). Default: current directory.

.PARAMETER Servers
  Which local servers to wire. Default: edge-npu, edge-gpu, edge-verify.
  ("The two local models" are npu+gpu; verify is the free, deterministic gate
  the delegation policy in CLAUDE.md depends on — kept on by default.)

.PARAMETER WithCloud
  Also wire edge-cloud (Tier 4, PAID, credit-guarded). Off by default.

.PARAMETER SkipSync
  Skip the `uv sync` dependency check (faster relaunch).

.PARAMETER Check
  Probe-only: print the supervisor's dependency table (never starts anything),
  smoke each wired server's import, and exit WITHOUT launching. Exits 1 if a
  critical dependency (docker, redis, worker) is down.

.PARAMETER NoSummary
  Skip the launch-time system summary (SD-1). The summary calls each wired
  server's status tool and prints a per-tier READY/DEGRADED line so a tier
  outage is visible at launch instead of buried in a `.rec` payload. Costs
  ~9s on the first NPU compile of the day; subsequent launches are fast.
  Use during dev when you're relaunching constantly and trust the wiring.

.PARAMETER NoDashboard
  Don't start the SD-3 dashboard when the supervisor finds it down (it is
  otherwise spawned in its own window with START_FROM_EOF=1, session-coupled,
  and the browser opened at http://localhost:8789). A dashboard already up on
  8789 is left alone either way.

.PARAMETER Canvas
  Deprecated no-op, kept so old invocations still work: standing up the Redis
  broker + Celery worker is now what the supervisor does on every launch.

.PARAMETER NoSupervise
  Skip the supervisor entirely (no probes, no restarts, no dashboard) for fast
  dev relaunches when you know the pipeline is already up.

.PARAMETER Force
  Launch Claude even if a critical dependency could not be brought up. Routes
  will fail until it is; the supervisor table says what to fix.

.PARAMETER NoBrowser
  Skip wiring the Playwright browser MCP (`playwright`). It is wired by
  default -- $0, local headed Chromium via `npx @playwright/mcp@<pinned>`, so it
  never breaks the no-spend invariant. Artifacts (screenshots, snapshots) land
  in runs\playwright\, not the project dir. Skipped with a warning when `npx`
  is not on PATH. Use -NoBrowser to save the Node startup on dev relaunches.

.EXAMPLE
  # One-time: install the `edge` PATH shim, then just type `edge` anywhere:
  powershell -ExecutionPolicy Bypass -File scripts\install-edge-shim.ps1
  edge                # = edge-cli.ps1 with defaults, in the current dir
  edge -Check         # every edge-cli.ps1 flag passes through

  # Windows PowerShell 5.1 (default on this machine — no `pwsh`):
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 -ProjectDir C:\src\myapp
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 -Check    # verify wiring, don't launch
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 -NoSupervise  # fast relaunch, pipeline known up
#>
[CmdletBinding()]
param(
  [string]   $ProjectDir = (Get-Location).Path,
  [string[]] $Servers    = @('edge-npu', 'edge-gpu', 'edge-verify'),
  [switch]   $WithCloud,
  [switch]   $SkipSync,
  [switch]   $Check,
  [switch]   $NoSummary,
  [switch]   $NoDashboard,
  [switch]   $Canvas,      # deprecated no-op: the supervisor always does this now
  [switch]   $NoBrowser,
  [switch]   $NoSupervise,
  [switch]   $Force
)

$ErrorActionPreference = 'Stop'

# --- repo + venv (resolved relative to THIS script, not the caller's cwd) ---
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
  throw "venv python not found at $VenvPython - run 'uv sync --extra accel --extra mcp --extra celery' in $RepoRoot first"
}
if ($Canvas) {
  Write-Host "[edge-cli] -Canvas is now the default (the supervisor keeps broker + worker up) - flag ignored" -ForegroundColor DarkGray
}

# --- Phase 0: propagate main-tree NPU model dir so worktrees with empty
# models/ still resolve. cascade/config.py reads CASCADE_NPU_MODEL_DIR if set;
# we point it at $RepoRoot/models/qwen2.5-coder-1.5b-npu (the path lives
# ALONGSIDE THIS SCRIPT, regardless of which ProjectDir we launch into).
# Skipped when the user has already exported the var.
if (-not $env:CASCADE_NPU_MODEL_DIR) {
  $MainNpuModelDir = Join-Path $RepoRoot 'models\qwen2.5-coder-1.5b-npu'
  if (Test-Path $MainNpuModelDir) {
    $env:CASCADE_NPU_MODEL_DIR = $MainNpuModelDir
    Write-Host "[edge-cli] CASCADE_NPU_MODEL_DIR=$MainNpuModelDir" -ForegroundColor DarkGray
  }
}

# --- locate the bundled Claude Code CLI (survives extension updates) ---
function Resolve-ClaudeCli {
  $cmd = Get-Command claude -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $ext = Get-ChildItem "$env:USERPROFILE\.vscode\extensions" -Directory -ErrorAction SilentlyContinue |
         Where-Object Name -like 'anthropic.claude-code-*' |
         Sort-Object Name -Descending |
         Select-Object -First 1
  if ($ext) {
    $p = Join-Path $ext.FullName 'resources\native-binary\claude.exe'
    if (Test-Path $p) { return $p }
  }
  $pkg = Get-ChildItem "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code" `
           -Directory -Recurse -ErrorAction SilentlyContinue |
         Sort-Object Name -Descending | Select-Object -First 1
  if ($pkg) {
    $p = Join-Path $pkg.FullName 'claude.exe'
    if (Test-Path $p) { return $p }
  }
  throw "Could not locate the claude CLI. Install Claude Code or add it to PATH."
}
$ClaudeCli = Resolve-ClaudeCli

# --- dependency check (idempotent; fast when already satisfied) ---
# --inexact: ensure edge-cli's required extras are present WITHOUT removing
# others. Without it, `uv sync --extra accel --extra mcp` is enforcing -- it
# purges every other extra (imagegen/celery/llama_cpp) on each launch, breaking
# the SDXL image server, Celery Canvas, and llama-cpp direct-load tiers for any
# user who launches edge-cli after setting them up. See memory:
# edge-cascade-imagegen-env-setup for the failure mode that motivated this.
if (-not $SkipSync) {
  # `celery` = the supervisor's broker/worker probes + the worker itself.
  # --inexact so it never purges accel/mcp/imagegen/llama_cpp.
  $syncExtras = @('--extra', 'accel', '--extra', 'mcp', '--extra', 'celery')
  Write-Host "[edge-cli] uv sync --inexact $($syncExtras -join ' ') ..." -ForegroundColor Cyan
  Push-Location $RepoRoot
  try { uv sync --inexact @syncExtras | Out-Null } finally { Pop-Location }
}

# --- generate the machine-correct local MCP config ---------------------------
# Absolute command + explicit cwd + PYTHONPATH so the servers work no matter
# which directory the user is building a project in.
$catalog = @{
  'edge-npu'    = @('-m', 'mcp_servers.npu')
  'edge-gpu'    = @('-m', 'mcp_servers.gpu')
  'edge-verify' = @('-m', 'mcp_servers.verify')
  'edge-cloud'  = @('-m', 'mcp_servers.cloud')
}
$wanted = [System.Collections.Generic.List[string]]::new()
$Servers | ForEach-Object { if ($catalog.ContainsKey($_)) { $wanted.Add($_) } else { Write-Warning "unknown server '$_' - skipped" } }
if ($WithCloud -and -not $wanted.Contains('edge-cloud')) { $wanted.Add('edge-cloud') }
if ($wanted.Count -eq 0) { throw "no valid servers selected" }

$mcpServers = @{}
foreach ($name in $wanted) {
  $env_dict = @{ PYTHONPATH = $RepoRoot }
  if ($env:CASCADE_NPU_MODEL_DIR) { $env_dict.CASCADE_NPU_MODEL_DIR = $env:CASCADE_NPU_MODEL_DIR }
  $mcpServers[$name] = [ordered]@{
    command = $VenvPython
    args    = $catalog[$name]
    cwd     = $RepoRoot
    env     = $env_dict
  }
}

# Playwright browser MCP (default on; -NoBrowser opts out). Not a cascade tier:
# a $0 third-party npx server, so it lives outside $catalog/$Servers. Version is
# PINNED so an upstream release can't silently change a working session; bump
# deliberately. Absolute npx.cmd path, same rationale as $VenvPython above.
# --output-dir keeps screenshots out of the user's ProjectDir (runs/ is ignored).
$PlaywrightMcp = '@playwright/mcp@0.0.82'
$BrowserWired  = $false
if (-not $NoBrowser) {
  $npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
  if ($npx) {
    $mcpServers['playwright'] = [ordered]@{
      command = $npx.Source
      args    = @('-y', $PlaywrightMcp, '--output-dir', (Join-Path $RepoRoot 'runs\playwright'))
    }
    $wanted.Add('playwright')
    $BrowserWired = $true
  } else {
    Write-Warning "[edge-cli] npx not on PATH - Playwright browser MCP not wired (install Node.js, or pass -NoBrowser to silence)."
  }
}

$ConfigPath = Join-Path $RepoRoot 'runs\edge-local.mcp.json'
$json = @{ mcpServers = $mcpServers } | ConvertTo-Json -Depth 8
# Windows PowerShell 5.1's `Out-File -Encoding utf8` prepends a BOM, which a
# strict JSON parser (Claude Code reads this file) rejects. Write UTF-8 *no BOM*.
[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding $false))
Write-Host "[edge-cli] wired: $($wanted -join ', ')" -ForegroundColor Green
if (-not $WithCloud) {
  Write-Host "[edge-cli] Tier 4 (edge-cloud / paid API) NOT wired - session cannot spend." -ForegroundColor Yellow
}

# --- EDGE-1 supervisor: probe every pipeline dependency, restart what's down --
# `edge` must leave the Canvas pipeline UP before it launches a session. The
# decisions live in cascade/health.py (covered, unit-tested): which deps are
# down, the repair order, and which to re-probe first because a prerequisite
# was down too. This block is only the process-spawning glue around it.
#
#   UP         healthy at launch             RECOVERED  came back on its own (no spawn)
#   RESTARTED  started here, now answering   FAILED     start/wait failed (+ fix)
#   DOWN       -Check only (never starts)    SKIPPED    -NoDashboard
#
# A critical dep (docker, redis, worker) not UP -> exit 1 BEFORE launching
# Claude, unless -Force. Everything is idempotent: a warm `edge` starts nothing.

function Invoke-EdgeHealth {
  # `python -m cascade.health --json [--only <dep>]...` -> parsed object with
  # .statuses, .repairs, .prereqs. EAP=Continue: under 'Stop', PS 5.1 turns a
  # native exe's stderr line into a terminating error.
  param([string[]]$Only = @())
  $ErrorActionPreference = 'Continue'
  $healthArgs = @('-m', 'cascade.health', '--json')
  foreach ($n in $Only) { $healthArgs += @('--only', $n) }
  Push-Location $RepoRoot
  try { $out = & $VenvPython @healthArgs 2>$null } finally { Pop-Location }
  if (-not $out) { throw "cascade.health produced no output (is the celery extra synced?)" }
  ($out | Out-String) | ConvertFrom-Json
}

function Wait-EdgeDep {
  # Re-probe one dep every 2s until it is UP or $TimeoutSec elapses.
  param([string]$Name, [int]$TimeoutSec)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  do {
    $s = (Invoke-EdgeHealth -Only $Name).statuses[0]
    if ($s.up) { return $s }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)
  $s.detail = "not up after ${TimeoutSec}s: $($s.detail)"
  $s
}

function Get-EdgeWorkerProcess {
  # Any live Celery worker for this app (plain or -Watch). A worker that exists
  # but doesn't answer ping yet (booting, reconnecting to a restarted broker)
  # must be waited on, not duplicated -- the bug EDGE-1 exists to fix.
  @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'cascade\.celery_app' -and $_.CommandLine -match '\bworker\b' })
}

# name -> scriptblock that starts it. Emits nothing, or 'waited' when it found the
# dep already starting and left it alone (-> RECOVERED); Wait-EdgeDep decides UP.
$EdgeStarters = @{
  docker = {
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $exe)) { throw "Docker Desktop not found at $exe" }
    Start-Process $exe | Out-Null
  }
  redis = {
    # compose prints progress on stderr; under 'Stop' PS 5.1 would throw on it.
    $ErrorActionPreference = 'Continue'
    Push-Location $RepoRoot
    try { docker compose up -d redis 2>&1 | Out-Null } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "docker compose up -d redis exited $LASTEXITCODE" }
  }
  ollama = {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) { throw "ollama not on PATH" }
    Start-Process $ollama.Source -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
  }
  worker = {
    $existing = Get-EdgeWorkerProcess
    if ($existing) {
      Write-Host "[edge-cli]   worker process already running (pid $($existing[0].ProcessId)) - waiting for it, not spawning a duplicate" -ForegroundColor DarkGray
      return 'waited'
    }
    # Slice-5 launcher: python -m celery (WDAC), --pool=solo, refuses `cloud`
    # (spend invariant). Resident in its own window; outlives the session.
    Start-Process powershell -ArgumentList @(
      '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot '_celery-worker.ps1'),
      '-Queues', 'npu,gpu,verify', '-NodeName', 'edge-local',
      '-PropagateNpuModelDir', '-SkipSync'
    ) | Out-Null
  }
  dashboard = {
    # SD-3: session-coupled (START_FROM_EOF=1) so it shows only this session's
    # records. Env is set inside the child command, leaving Claude's env alone.
    $dashboardDir = Join-Path $RepoRoot 'dashboard'
    if (-not (Test-Path $dashboardDir)) { throw "dashboard dir not found at $dashboardDir" }
    $childCmd = "`$env:RUNS_DIR='$(Join-Path $RepoRoot 'runs')'; `$env:START_FROM_EOF='1'; npm start"
    Start-Process powershell -WorkingDirectory $dashboardDir `
      -ArgumentList '-NoExit', '-Command', $childCmd | Out-Null
  }
}
# Bounded waits. Docker Desktop's cold start is the slow one.
$EdgeTimeouts = @{ docker = 120; redis = 30; ollama = 30; worker = 60; dashboard = 10 }
$EdgeFixHints = @{
  docker    = 'start Docker Desktop, wait for the engine, rerun edge'
  redis     = "docker compose up -d redis   (in $RepoRoot)"
  ollama    = 'ollama serve'
  worker    = 'scripts\_celery-worker.ps1 -Queues npu,gpu,verify   (read its window for the traceback)'
  dashboard = 'cd dashboard; npm start'
}

$EdgeCriticalDown = @()
if (-not $NoSupervise) {
  Write-Host "[edge-cli] supervisor: probing pipeline dependencies ..." -ForegroundColor Cyan
  $health = Invoke-EdgeHealth
  $state = [ordered]@{}
  foreach ($s in $health.statuses) {
    $state[$s.name] = @{ status = $s; result = $(if ($s.up) { 'UP' } else { 'DOWN' }) }
  }
  if (-not $Check) {
    foreach ($repair in $health.repairs) {
      $name, $reprobeFirst = $repair[0], $repair[1]
      $prev = $state[$name].status
      if ($name -eq 'dashboard' -and $NoDashboard) { $state[$name].result = 'SKIPPED'; continue }
      $prereq = $health.prereqs.$name
      if ($prereq -and -not $state[$prereq].status.up) {
        $prev.detail = "not started: $prereq is down"
        $state[$name].result = 'FAILED'
        continue
      }
      if ($reprobeFirst) {
        # Its prereq was down and is now back: redis returns with the engine
        # (restart: unless-stopped) and a live worker reconnects on its own.
        $s = (Invoke-EdgeHealth -Only $name).statuses[0]
        if ($s.up) { $state[$name] = @{ status = $s; result = 'RECOVERED' }; continue }
      }
      Write-Host "[edge-cli]   starting $name (up to $($EdgeTimeouts[$name])s) ..." -ForegroundColor Cyan
      try {
        $started = if ((& $EdgeStarters[$name]) -eq 'waited') { 'RECOVERED' } else { 'RESTARTED' }
        $s = Wait-EdgeDep -Name $name -TimeoutSec $EdgeTimeouts[$name]
      } catch {
        $s = [pscustomobject]@{ name = $name; up = $false; critical = $prev.critical
                                detail = "start failed: $($_.Exception.Message)" }
      }
      $state[$name] = @{ status = $s; result = $(if ($s.up) { $started } else { 'FAILED' }) }
      if ($name -eq 'dashboard' -and $s.up) {
        # Best-effort browser open, only for a dashboard this launch started.
        try { Start-Process 'http://localhost:8789' -ErrorAction Stop | Out-Null }
        catch { Write-Warning "[edge-cli] could not auto-open browser: $($_.Exception.Message)" }
      }
    }
  }
  $colors = @{ UP = 'Green'; RECOVERED = 'Green'; RESTARTED = 'Yellow'; SKIPPED = 'DarkGray'; DOWN = 'Red'; FAILED = 'Red' }
  foreach ($entry in $state.GetEnumerator()) {
    $s, $result = $entry.Value.status, $entry.Value.result
    Write-Host ('  {0,-10} {1,-10} {2}' -f $s.name, $result, $s.detail) -ForegroundColor $colors[$result]
    if (-not $s.up -and $result -ne 'SKIPPED') {
      $optional = if ($s.critical) { '' } else { '   (optional - launch continues)' }
      Write-Host "             fix: $($EdgeFixHints[$s.name])$optional" -ForegroundColor DarkGray
    }
  }
  $EdgeCriticalDown = @($state.Values | Where-Object { $_.status.critical -and -not $_.status.up } |
                        ForEach-Object { $_.status.name })
  if ($EdgeCriticalDown -and -not $Check) {
    if (-not $Force) {
      Write-Host "[edge-cli] pipeline NOT up (critical: $($EdgeCriticalDown -join ', ')) - not launching. Fix the above, or pass -Force to launch anyway." -ForegroundColor Red
      exit 1
    }
    Write-Warning "[edge-cli] -Force: launching with critical deps down ($($EdgeCriticalDown -join ', ')) - routes will fail until they're up."
  }
}

# --- launch-time system summary (SD-1) --------------------------------------
# Closes the Phase A visibility gap (#57): every wired tier's readiness is
# printed in plain text BEFORE Claude launches, so an `available:false` tier
# is impossible to miss. The Python helper speaks real MCP stdio against each
# server -- same wire path the launched session will use -- so what the
# operator sees here is exactly what Claude will see.
# Skipped under -Check: -Check's own import-only smoke is the cheaper probe
# this flag was designed for; running the summary too would spin every MCP
# server twice (~9s NPU compile doubled).
if (-not $NoSummary -and -not $Check) {
  Write-Host ""
  Write-Host "[edge-cli system summary]" -ForegroundColor Cyan
  Write-Host "  cwd:     $ProjectDir"
  # Push/Pop in a try/finally so a failure between them never leaves the
  # location stack imbalanced. -ErrorAction SilentlyContinue on Pop covers
  # the (rare) case where Push itself failed -- there's nothing to pop.
  try {
    Push-Location $ProjectDir
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    $sha    = (git rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $branch) {
      Write-Host "  branch:  $branch @ $sha"
    }
  } finally {
    Pop-Location -ErrorAction SilentlyContinue
  }
  Write-Host "  cascade:"
  $SummaryScript = Join-Path $RepoRoot 'scripts\edge_summary.py'
  & $VenvPython $SummaryScript $ConfigPath
  Write-Host ""
}

# --- optional pre-launch smoke ----------------------------------------------
if ($Check) {
  $probe = @{ 'edge-npu'='status'; 'edge-gpu'='status'; 'edge-verify'='verify_syntax'; 'edge-cloud'='budget' }
  foreach ($name in $wanted) {
    # playwright is npx, not a Python module; resolving npx.cmd above is its
    # cheap check (a real spawn would download/boot Node for a wiring probe).
    if ($name -eq 'playwright') { Write-Host "[check] playwright ($PlaywrightMcp via npx) ... OK"; continue }
    $mod = ($catalog[$name])[1]
    Write-Host "[check] $name ($mod -> $($probe[$name])) ..." -NoNewline
    # The pytest/MCP smoke already proves these start; here we just confirm the
    # module imports cleanly under the venv (cheap, no model load).
    Push-Location $RepoRoot
    try {
      & $VenvPython -c "import importlib,sys; importlib.import_module('$mod'); print(' OK')"
    } catch { Write-Host " FAIL"; throw } finally { Pop-Location }
  }
  if ($EdgeCriticalDown) {
    Write-Host "[edge-cli] check FAILED - critical deps down: $($EdgeCriticalDown -join ', ') (run edge without -Check to start them)." -ForegroundColor Red
    exit 1
  }
  Write-Host "[edge-cli] check passed - not launching (remove -Check to launch)." -ForegroundColor Green
  return
}

# --- launch ------------------------------------------------------------------
if (-not (Test-Path $ProjectDir)) { throw "ProjectDir not found: $ProjectDir" }

# The delegation policy MUST travel with the session: it launches in the user's
# project dir (not the repo), so edge-cascade\CLAUDE.md is NOT auto-discovered,
# and --add-dir only grants file access, not policy. Inject the operative rules
# straight into the system prompt via --append-system-prompt, and point at the
# full protocol file (the session has --add-dir read access to it).
# Single line, ASCII, no backtick/$/embedded-quote -> safe as a PS 5.1 exe arg.
$PolicyFile = Join-Path $RepoRoot 'CLAUDE.md'
$policy = (
  'You are Tier 3 of the edge-cascade local-first inference mesh. ' +
  'MCP tools available: edge-npu (route, draft), edge-gpu (generate), ' +
  'edge-verify (verify_syntax, verify_functional, repair_prompt). ' +
  'MANDATORY: for ANY non-trivial code generation, FIRST call edge-npu.route, ' +
  'then draft via edge-npu.draft or edge-gpu.generate, then gate the result ' +
  'with edge-verify before using it. Only write code yourself if the local ' +
  'tiers fail verification twice. Orchestration is yours: file edits, ' +
  'commands, planning, and conversational replies stay with you and need no ' +
  'delegation. Never claim a local tier ran or wrote anything. The paid ' +
  'Anthropic API tier is NOT wired in - do not attempt it. Read the full ' +
  'protocol and the routing_dispatch format in ' + $PolicyFile +
  ' before your first coding task.'
)
if ($BrowserWired) {
  $policy += (
    ' The playwright MCP (browser automation: navigate, snapshot, click, ' +
    'screenshot) is wired for browser work - e2e checks, UI verification, ' +
    'dashboard screenshots. It is not a code-generation tier and never ' +
    'replaces the routing policy above.'
  )
}

Write-Host "[edge-cli] launching Claude CLI in $ProjectDir" -ForegroundColor Cyan
Write-Host "[edge-cli] cli: $ClaudeCli" -ForegroundColor DarkGray
Write-Host "[edge-cli] delegation policy injected via --append-system-prompt" -ForegroundColor Green
$claudeArgs = @(
  '--mcp-config', $ConfigPath,
  '--strict-mcp-config',
  '--add-dir', $RepoRoot,
  '--append-system-prompt', $policy
)
Push-Location $ProjectDir
try {
  & $ClaudeCli @claudeArgs
} finally {
  Pop-Location
}
