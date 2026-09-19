<#
.SYNOPSIS
  Install an `edge` command on PATH that launches edge-cli.ps1 with defaults.

.DESCRIPTION
  Writes <BinDir>\edge.cmd, a one-line forwarder:
    powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\edge-cli.ps1 %*
  so `edge` from any directory launches the cascade session in that directory,
  and every edge-cli.ps1 flag passes through (`edge -Check`, `edge -Canvas`,
  `edge -NoBrowser`, ...). The repo path is baked in at install time; re-run
  this script if the repo moves. Idempotent (overwrites the shim).

.PARAMETER BinDir
  Where to write edge.cmd. Default: $env:USERPROFILE\.local\bin (uv's bin dir,
  already on PATH on this machine). Warns if BinDir is not on PATH.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-edge-shim.ps1
#>
[CmdletBinding()]
param(
  [string] $BinDir = (Join-Path $env:USERPROFILE '.local\bin')
)

$ErrorActionPreference = 'Stop'

$Launcher = Join-Path $PSScriptRoot 'edge-cli.ps1'
if (-not (Test-Path $Launcher)) { throw "edge-cli.ps1 not found at $Launcher" }

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Shim = Join-Path $BinDir 'edge.cmd'
$body = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$Launcher`" %*`r`n"
# ASCII: cmd.exe misreads a UTF-8 BOM as part of the first command.
[System.IO.File]::WriteAllText($Shim, $body, [System.Text.Encoding]::ASCII)
Write-Host "[edge-shim] wrote $Shim -> $Launcher" -ForegroundColor Green

$onPath = ($env:PATH -split ';') | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') }
if (-not $onPath) {
  Write-Warning "[edge-shim] $BinDir is not on PATH - add it, or re-run with -BinDir <a dir on PATH>."
}
