<#
.SYNOPSIS
  Start the Intel-box Celery worker: Tier-1 NPU drafter (OpenVINO).
  Subscribes the `npu` queue. See docs/BARE-METAL-CELERY.md.

.EXAMPLE
  # broker on another box, redis with a password:
  powershell -ExecutionPolicy Bypass -File scripts\start-worker-intel.ps1 `
      -RedisHost 10.0.0.5 -RedisPassword s3cret
.EXAMPLE
  # single-box dev (broker on localhost):
  powershell -ExecutionPolicy Bypass -File scripts\start-worker-intel.ps1
#>
[CmdletBinding()]
param(
  [string] $RedisHost = $env:CASCADE_REDIS_HOST,
  [string] $RedisPassword = $env:CASCADE_REDIS_PASSWORD,
  [string] $Queues = 'npu',         # add ',verify' here if the RTX box doesn't run it
  [switch] $SkipSync
)
# `accel` = OpenVINO/NPU deps; PropagateNpuModelDir resolves the 1.5B export.
& "$PSScriptRoot\_celery-worker.ps1" -Queues $Queues -NodeName 'intel-npu' `
    -SyncExtras @('celery', 'accel') -PropagateNpuModelDir `
    -RedisHost $RedisHost -RedisPassword $RedisPassword -SkipSync:$SkipSync
