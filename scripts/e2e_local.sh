#!/usr/bin/env bash
# End-to-end happy-path proof for the local edge cascade.
#
# *** LOCAL ONLY -- never runs in CI. ***
# It drives the real MCP servers on real Intel iGPU + the loaded Tier-1 model,
# which CI runners do not have (no Intel NPU/iGPU, the heavy `accel` extra is
# not synced there). The driver SKIPS cleanly (exit 0) when those prerequisites
# are absent, so a hardware-less machine or CI is never blocked; on a developer
# box with the hardware it actually runs and a failure blocks the push.
#
# Wired as a pre-push hook (.pre-commit-config.yaml, stages: [pre-push]).
# Run manually:  bash scripts/e2e_local.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# iGPU Tier-1: bounded runtime, skips the abortable vpux NPU probe. No cloud
# tier -> zero network spend on every push.
export CASCADE_SKIP_NPU=1
export CASCADE_ENABLE_CLOUD=0

echo "[e2e] local pipeline happy-path proof (route -> draft -> verify)"
# Python self-bounds Tier-1 at 90s and exits 0 (skip). This `timeout` is only
# a last-resort safety net so a pathological hang can never wedge `git push`.
exec timeout 300 .venv/Scripts/python.exe -u scripts/e2e_local.py
