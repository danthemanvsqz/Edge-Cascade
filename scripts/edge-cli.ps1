<#
.SYNOPSIS
  Stand up the Claude CLI as Tier 3 of the edge cascade, wired to the LOCAL
  inference mesh only (Tier 1 NPU + Tier 2 GPU + the deterministic verifier).

.DESCRIPTION
  1. Ensures the edge-cascade venv has the `accel` + `mcp` extras.
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
  Smoke each wired server (status/no-spend tool) and exit WITHOUT launching.

.PARAMETER NoSummary
  Skip the launch-time system summary (SD-1). The summary calls each wired
  server's status tool and prints a per-tier READY/DEGRADED line so a tier
  outage is visible at launch instead of buried in a `.rec` payload. Costs
  ~9s on the first NPU compile of the day; subsequent launches are fast.
  Use during dev when you're relaunching constantly and trust the wiring.

.PARAMETER NoDashboard
  Skip the SD-3 dashboard auto-launch. The dashboard is normally spawned in a
  separate PowerShell window with START_FROM_EOF=1 (session-coupled) and the
  default browser is pointed at http://localhost:8789. Use this when you're
  driving a non-interactive run, headless CI, or already have the dashboard
  open and don't want a second instance fighting for port 8789.

.EXAMPLE
  # Windows PowerShell 5.1 (default on this machine — no `pwsh`):
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 -ProjectDir C:\src\myapp
  powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 -Check   # verify wiring, don't launch
#>
[CmdletBinding()]
param(
  [string]   $ProjectDir = (Get-Location).Path,
  [string[]] $Servers    = @('edge-npu', 'edge-gpu', 'edge-verify'),
  [switch]   $WithCloud,
  [switch]   $SkipSync,
  [switch]   $Check,
  [switch]   $NoSummary,
  [switch]   $NoDashboard
)

$ErrorActionPreference = 'Stop'

# --- repo + venv (resolved relative to THIS script, not the caller's cwd) ---
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
  throw "venv python not found at $VenvPython - run 'uv sync --extra accel --extra mcp' in $RepoRoot first"
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
if (-not $SkipSync) {
  Write-Host "[edge-cli] uv sync --extra accel --extra mcp ..." -ForegroundColor Cyan
  Push-Location $RepoRoot
  try { uv sync --extra accel --extra mcp | Out-Null } finally { Pop-Location }
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
  $mcpServers[$name] = [ordered]@{
    command = $VenvPython
    args    = $catalog[$name]
    cwd     = $RepoRoot
    env     = @{ PYTHONPATH = $RepoRoot }
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

# --- SD-3: auto-launch the dashboard in a separate console ------------------
# Spawns a NEW PowerShell window running `npm start` in dashboard/, with
# RUNS_DIR pinned at the repo's runs/ and START_FROM_EOF=1 so the renderer
# only shows records appended during THIS edge-cli session (not whatever the
# gitignored runs/ history carries across launches). Then best-effort opens
# the default browser at http://localhost:8789 ONLY AFTER the Node server has
# bound the port (otherwise the first GET races the listen and hits ERR_CONN).
#
# Pre-existing listener on 8789 -> skip the spawn entirely with a warning
# (PR #60 review nit): the silent-collision case (new child crashes on listen,
# browser opens to the OLD session's dashboard) would silently defeat
# session-coupling. Better to tell the user the old dashboard is still up.
#
# Skipped under -Check (same rationale as the summary -- -Check is a wiring
# probe, not a full session) and under -NoDashboard (opt-out for headless /
# non-interactive runs / when the dashboard is already up on 8789).

function Test-EdgeCliPortBound {
  # Returns $true iff something is accepting on 127.0.0.1:$Port.
  # Uses raw TcpClient rather than Get-NetTCPConnection so we don't pick up
  # half-bound IPv6-only listeners as IPv4 ports (and vice versa), and so we
  # don't depend on the NetTCPIP module (present on Win10+, but the launcher
  # should be conservative). 200 ms is enough on loopback; longer would block
  # the launch path for nothing.
  param([int]$Port = 8789, [int]$TimeoutMs = 200)
  $tcp = New-Object System.Net.Sockets.TcpClient
  try {
    $iar = $tcp.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
    try { $tcp.EndConnect($iar); return $true } catch { return $false }
  } finally { $tcp.Close() }
}

if (-not $NoDashboard -and -not $Check) {
  $DashboardDir = Join-Path $RepoRoot 'dashboard'
  $RunsDir      = Join-Path $RepoRoot 'runs'
  if (-not (Test-Path $DashboardDir)) {
    Write-Warning "[edge-cli] dashboard dir not found at $DashboardDir - skipping auto-launch"
  } elseif (Test-EdgeCliPortBound -Port 8789) {
    # Pre-existing dashboard on 8789. Spawning a second one would crash on
    # listen and leave the browser pointing at the OLD session, silently
    # defeating session-coupling. Skip + tell the user.
    Write-Warning "[edge-cli] port 8789 is already bound - a previous dashboard is still running."
    Write-Warning "[edge-cli] kill that window (or pass -NoDashboard) to silence this; not opening browser."
  } else {
    Write-Host "[edge-cli] launching dashboard (session-coupled) -> http://localhost:8789" -ForegroundColor Cyan
    # The child PS process inherits no env that isn't explicitly set in -Command.
    # Setting `$env:RUNS_DIR` / `$env:START_FROM_EOF` inline here keeps the
    # to-be-launched Claude session's env completely untouched.
    $childCmd = "`$env:RUNS_DIR='$RunsDir'; `$env:START_FROM_EOF='1'; npm start"
    Start-Process powershell `
      -WorkingDirectory $DashboardDir `
      -ArgumentList '-NoExit', '-Command', $childCmd | Out-Null
    # Poll the port until the child binds (or 5 s elapses) so the browser
    # doesn't race the listen and get ERR_CONNECTION_REFUSED on first GET.
    # 5 s covers cold-cache `npm start` on this machine; longer means
    # something is structurally wrong and the user should look at the new
    # console themselves rather than wait silently.
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
      if (Test-EdgeCliPortBound -Port 8789) { break }
      Start-Sleep -Milliseconds 200
    }
    if (-not (Test-EdgeCliPortBound -Port 8789)) {
      Write-Warning "[edge-cli] dashboard did not bind 8789 within 5s - check the spawned console; opening browser anyway"
    }
    # Best-effort browser open (default browser). Non-blocking; failures here
    # mustn't take down the launch (e.g. headless WSL, no DEFAULT verb).
    try { Start-Process 'http://localhost:8789' -ErrorAction Stop | Out-Null }
    catch { Write-Warning "[edge-cli] could not auto-open browser: $($_.Exception.Message)" }
  }
}

# --- optional pre-launch smoke ----------------------------------------------
if ($Check) {
  $probe = @{ 'edge-npu'='status'; 'edge-gpu'='status'; 'edge-verify'='verify_syntax'; 'edge-cloud'='budget' }
  foreach ($name in $wanted) {
    $mod = ($catalog[$name])[1]
    Write-Host "[check] $name ($mod -> $($probe[$name])) ..." -NoNewline
    # The pytest/MCP smoke already proves these start; here we just confirm the
    # module imports cleanly under the venv (cheap, no model load).
    Push-Location $RepoRoot
    try {
      & $VenvPython -c "import importlib,sys; importlib.import_module('$mod'); print(' OK')"
    } catch { Write-Host " FAIL"; throw } finally { Pop-Location }
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
