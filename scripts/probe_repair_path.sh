#!/usr/bin/env bash
# Regression gate for the local repair/escalation path (NPU -> verify -> GPU
# -> re-gate -> 2-round cap), driven over real MCP stdio.
#
# *** LOCAL ONLY -- never runs in CI. ***
# Needs the `accel` extra + Intel hardware + Ollama (qwen2.5-coder:14b). When
# any of that is absent the gate SKIPs cleanly (exit 0) so a hardware-less box
# or CI is never blocked; on a dev box with the hardware a true regression
# (verifier stops rejecting the known-bad draft, or any paid-tier call) blocks
# the push. The stochastic GPU outcome ([OK] vs [CAP]) is NOT a failure.
#
# Wired as a pre-push hook (.pre-commit-config.yaml, stages: [pre-push]).
# Run manually:  bash scripts/probe_repair_path.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Bounded iGPU Tier-1: skip the abortable vpux NPU probe (it can hard-abort the
# process and must never wedge `git push`). No cloud tier -> zero spend.
export CASCADE_SKIP_NPU=1
export CASCADE_ENABLE_CLOUD=0

echo "[probe-gate] local repair-path regression gate (npu->verify->gpu)"
# Last-resort wall-clock net so a pathological hang can never wedge the push;
# the Python gate already self-skips on unavailable hardware/Ollama.
exec timeout 420 .venv/Scripts/python.exe -u scripts/probe_repair_path.py --gate
