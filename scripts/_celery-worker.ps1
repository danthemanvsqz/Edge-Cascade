<#
.SYNOPSIS
  Shared launcher for a bare-metal Celery tier worker (Phase 2 Slice 5).
  Not called directly -- use start-worker-intel.ps1 / start-worker-rtx.ps1.

.DESCRIPTION
  Starts ONE resident Celery worker subscribed to the given tier queues,
  pointed at a (possibly cross-box) Redis broker. Encodes the two Windows
  gotchas proven by the Phase-0 spike (docs/FINDINGS-canvas-repair-retry-spike.md):
    * `celery.exe` is blocked by WDAC App Control on this host (os error 4551)
      -> launch via `python -m celery`, never the console script.
    * the prefork pool is unreliable on Windows -> `--pool=solo`.
  Resident warm state (NPU ~12-21s compile, Ollama load) is preserved by
  `worker_max_tasks_per_child=0`, already set in cascade/celery_app.py.

.PARAMETER Queues
  Comma-separated tier queues this worker consumes, e.g. "npu" or "gpu,verify".
  The `cloud` queue is REFUSED (the structural spend invariant: no worker on
  `cloud` => the paid tier is unspendable, same guarantee as edge-cli's
  --strict-mcp-config exclusion). See cascade/tasks.py cloud_generate_task.

.PARAMETER NodeName
  Celery node-name prefix; the worker registers as <NodeName>@<hostname>.
  Node names MUST be unique across the cluster -- the @%h suffix makes them so
  per box.

.PARAMETER SyncExtras
  uv extras to ensure present before launch (via `uv sync --inexact` so other
  extras are NOT purged -- the edge-cli lesson, memory edge-cascade-imagegen).

.PARAMETER RedisHost
  Broker box hostname/IP. Sets CASCADE_REDIS_HOST for the worker process; the
  URL is assembled by cascade.celery_app._redis_url. Defaults to the inherited
  env (localhost when unset).

.PARAMETER RedisPassword
  Broker password iff redis runs with `requirepass`. Sets CASCADE_REDIS_PASSWORD.

.PARAMETER PropagateNpuModelDir
  Point CASCADE_NPU_MODEL_DIR at the main-tree models/ dir (the Intel box's NPU
  drafter needs it). Mirrors edge-cli.ps1.

.PARAMETER SkipSync
  Skip the `uv sync` dependency check (faster relaunch).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string] $Queues,
  [string]   $NodeName = 'worker',
  [string[]] $SyncExtras = @('celery'),
  [string]   $RedisHost = $env:CASCADE_REDIS_HOST,
  [string]   $RedisPassword = $env:CASCADE_REDIS_PASSWORD,
  [switch]   $PropagateNpuModelDir,
  [switch]   $SkipSync
)

$ErrorActionPreference = 'Stop'

# --- spend invariant: a tier worker must never consume the cloud queue -------
$queueList = $Queues.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
if ($queueList -contains 'cloud') {
  throw "refusing to start: the 'cloud' queue must have NO worker (spend invariant). Drop it from -Queues."
}
if ($queueList.Count -eq 0) { throw "no queues given in -Queues" }

# --- repo + venv (resolved relative to THIS script, not the caller's cwd) ----
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
  throw "venv python not found at $VenvPython - run 'uv sync --extra celery' in $RepoRoot first"
}

# --- broker wiring: parts -> env, read by celery_app._redis_url --------------
if ($RedisHost)     { $env:CASCADE_REDIS_HOST = $RedisHost }
if ($RedisPassword) { $env:CASCADE_REDIS_PASSWORD = $RedisPassword }
$brokerHost = if ($env:CASCADE_REDIS_HOST) { $env:CASCADE_REDIS_HOST } else { 'localhost' }

# --- Intel box: make the NPU model dir resolvable (mirror edge-cli.ps1) -------
if ($PropagateNpuModelDir -and -not $env:CASCADE_NPU_MODEL_DIR) {
  $MainNpuModelDir = Join-Path $RepoRoot 'models\qwen2.5-coder-1.5b-npu'
  if (Test-Path $MainNpuModelDir) {
    $env:CASCADE_NPU_MODEL_DIR = $MainNpuModelDir
    Write-Host "[worker] CASCADE_NPU_MODEL_DIR=$MainNpuModelDir" -ForegroundColor DarkGray
  }
}

# Servers/tasks import cascade.* from the repo root regardless of caller cwd.
$env:PYTHONPATH = $RepoRoot

# --- dependency check (idempotent; --inexact never purges sibling extras) -----
if (-not $SkipSync) {
  $extraArgs = $SyncExtras | ForEach-Object { @('--extra', $_) }
  Write-Host "[worker] uv sync --inexact $($extraArgs -join ' ') ..." -ForegroundColor Cyan
  Push-Location $RepoRoot
  try { uv sync --inexact @extraArgs | Out-Null } finally { Pop-Location }
}

# --- launch the resident worker ----------------------------------------------
# `python -m celery` (NOT celery.exe -> WDAC), solo pool (Windows), %h = hostname.
Write-Host "[worker] queues : $($queueList -join ', ')" -ForegroundColor Green
Write-Host "[worker] broker : redis @ $brokerHost" -ForegroundColor Green
Write-Host "[worker] node   : $NodeName@<hostname>" -ForegroundColor Green
Write-Host "[worker] Ctrl-C to stop." -ForegroundColor DarkGray

Push-Location $RepoRoot
try {
  # Task lifecycle events (for Flower's live-activity capture) are enabled at the
  # config level -- worker_send_task_events=True in cascade/celery_app.py -- so
  # every launch path emits them; no per-launch -E needed here.
  & $VenvPython -m celery -A cascade.celery_app worker `
      -Q ($queueList -join ',') `
      --pool=solo `
      --hostname "$NodeName@%h" `
      -l info
} finally {
  Pop-Location
}
