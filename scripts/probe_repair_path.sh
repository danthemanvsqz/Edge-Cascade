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

# Sampling. Each invocation of the probe spends ~30 s of wall time AND
# writes ~5-10 records into runs/edge-{npu,verify,gpu}.rec -- those records
# end up rendered in the dashboard as if they were real cascade work,
# drowning out actual sessions on a push-heavy day. Running every Nth push
# trims the noise + the wall time without giving up the regression-gate
# signal (a real regression will trip on the next sampled run anyway).
#
# CASCADE_PROBE_SAMPLE_RATE is a 1-in-N denominator:
#   "1"  -> run on every push (legacy behavior; for debugging regressions)
#   "5"  -> run on ~1 in 5 pushes (default)
#   "0"  -> never run (skip the probe entirely; useful for run-of-many-PRs)
PROBE_SAMPLE_RATE="${CASCADE_PROBE_SAMPLE_RATE:-5}"
if [[ "$PROBE_SAMPLE_RATE" == "0" ]]; then
  echo "[probe-gate] CASCADE_PROBE_SAMPLE_RATE=0; skip"
  exit 0
fi
if (( PROBE_SAMPLE_RATE > 1 )) && (( RANDOM % PROBE_SAMPLE_RATE != 0 )); then
  echo "[probe-gate] sampled out (1 in $PROBE_SAMPLE_RATE pushes); skip"
  exit 0
fi

# Bounded iGPU Tier-1: skip the abortable vpux NPU probe (it can hard-abort the
# process and must never wedge `git push`). No cloud tier -> zero spend.
export CASCADE_SKIP_NPU=1
export CASCADE_ENABLE_CLOUD=0

echo "[probe-gate] local repair-path regression gate (npu->verify->gpu)"
# Last-resort wall-clock net so a pathological hang can never wedge the push;
# the Python gate already self-skips on unavailable hardware/Ollama.
exec timeout 420 .venv/Scripts/python.exe -u scripts/probe_repair_path.py --gate
