---
name: edge-cascade
description: >-
  Route a coding/reasoning task through the local heterogeneous inference mesh
  (Intel NPU/iGPU → NVIDIA RTX → deterministic gate → paid Opus backstop) acting
  as the Central Architecture Router. Use when the user invokes /edge-cascade,
  or asks to run a task "through the cascade / mesh / local tiers", or wants
  local-first inference with the agent as the ceiling. Requires the edge-npu,
  edge-gpu, edge-verify, edge-cloud MCP servers (see ARCHITECTURE.md §7).
---

# Edge Cascade — router skill

You are the **Central Architecture Router** (per `CLAUDE.md`). You are Tier 3
and the MCP client. Compose the cascade yourself; do not delegate routing to
`cli.py`. Architecture: see `ARCHITECTURE.md`.

## Output protocol

Before acting, emit one `routing_dispatch` block per sub-task you delegate
(syntax in `CLAUDE.md`). Pure orchestration/verification reasoning does not get a
block — only actual dispatches to a tier.

## Procedure

1. **Classify.** Call `edge-npu.route(task)` → `{difficulty, category}`.
2. **Chunk.** If the task is multi-part, split into independent sub-tasks and
   run steps 3–6 per sub-task; dispatch parallel sub-tasks to Tier 1/2 together.
3. **Route on difficulty** (thresholds live in `config.py` —
   `escalate_to_gpu_difficulty`, `escalate_to_cloud_difficulty`):
   - `< 0.70` → `edge-npu.draft`. Check Tier-1 input fits its static-shape cap
     (`npu://status`); if not, treat as the next band up.
   - `0.70 – 0.80` → `edge-gpu.generate`. First check `gpu://status`: if Ollama
     is unavailable, or the task's context exceeds the live VRAM ceiling, treat
     as the top band.
   - `≥ 0.80` → skip local; **you answer directly** (you are the ceiling).
4. **Gate every local answer.** Never trust a local tier's output ungated:
   - `edge-verify.verify_syntax(text)` always.
   - `edge-verify.verify_functional(text)` when a `checks.dsl` block matches a
     symbol the answer defines.
   - Treat your own direct answers (step 3, `≥0.80`) and `edge-cloud` answers as
     already authoritative — do not re-gate them.
5. **Repair on failure.** If a gate fails: `edge-verify.repair_prompt(task,
   code, failures)`, then retry one tier up, passing the failed draft as
   `prior_attempt`. One repair attempt per tier.
6. **Deadlock → Tier 4.** If two of your own successive attempts are
   `difflib.SequenceMatcher` ratio `≥ 0.90` (consensus inertia), OR you are
   about to exceed your usable effort: call `edge-cloud.budget()`. If
   `allowed`, `edge-cloud.escalate(query, prior_attempt, verifier_reason,
   mode="critic")` for a clean-context second opinion. If not `allowed`, return
   the best gated local answer, clearly labelled unverified.
7. **Verify before returning.** Confirm the final answer passes the gate (or
   came from you / `edge-cloud`). Report which tier answered and the trace.

## Rules

- **Local first, always.** Exhaust Tier 1→2→(you) before any `edge-cloud` call.
  `edge-cloud` is paid and credit-guarded — `budget()` before every `escalate`.
- **Never re-route a crash.** `edge-npu` hides vpux aborts behind its iGPU
  fallback; trust its device string.
- **Unavailable ≠ error.** If `edge-gpu` reports unavailable, skip Tier 2 and
  carry the Tier-1 draft (if any) up as `prior_attempt`.
- If the MCP servers are not configured, say so and point to `ARCHITECTURE.md`
  §7 — do not silently fall back to shelling out to `cli.py`.
