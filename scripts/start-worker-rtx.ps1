<#
.SYNOPSIS
  Start the RTX-box Celery worker: Tier-2 GPU coder (Ollama/llama-cpp) plus the
  deterministic verify gate. Subscribes `gpu,verify`. See docs/BARE-METAL-CELERY.md.

.DESCRIPTION
  `verify` (sandboxed exec of the functional gate) needs no accelerator and can
  live on either box; it rides with the RTX worker by default so exactly one
  worker covers it. Move it to the Intel box (its `-Queues 'npu,verify'`) and
  drop it here (`-Queues gpu`) if you prefer.

  Default backend is Ollama-over-HTTP (no extra). For the llama-cpp direct-load
  backend (CASCADE_GPU_BACKEND=llama_cpp) add: -SyncExtras celery,llama_cpp.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-worker-rtx.ps1 `
      -RedisHost 10.0.0.5 -RedisPassword s3cret
#>
[CmdletBinding()]
param(
  [string]   $RedisHost = $env:CASCADE_REDIS_HOST,
  [string]   $RedisPassword = $env:CASCADE_REDIS_PASSWORD,
  [string]   $Queues = 'gpu,verify',
  [string[]] $SyncExtras = @('celery'),
  [switch]   $SkipSync
)
& "$PSScriptRoot\_celery-worker.ps1" -Queues $Queues -NodeName 'rtx-gpu' `
    -SyncExtras $SyncExtras `
    -RedisHost $RedisHost -RedisPassword $RedisPassword -SkipSync:$SkipSync
