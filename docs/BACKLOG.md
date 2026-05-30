# Backlog (groomed)

Live, prioritized backlog. Ordering and zones follow
[PRIORITIZATION.md](PRIORITIZATION.md) — a 4×4 **Impact × Severity** matrix:
impact descending, then severity ascending (safest first); the `I1` column is
dropped, the `S4` row is parked + de-risked.

> Last groomed: **2026-05-30** (after Phase 2 Slice 6 merged; main `ed7588c`).

## Current placement

```
 Severity ↓ \ Impact →   I1 Trivial   I2 Minor          I3 Major                 I4 Critical
 S1 Safe                  ✗ (none)     —                 #1 PT-1                  — (none)
 S2 Low                   ✗ (none)     #4 gate-helper    #2 PT-2                  — (none)
                                       #5 PT-4 verbump   (Slice 7: blocked)
 S3 Moderate              ✗ (none)     —                 #3 PT-3                  — (none)
 S4 Severe (park)         ✗ (none)     ⏳ none           ⏳ none                  ⏳ none
```

No `S4` park items and no `I1` drops right now. Slice 7 is **dependency-blocked**
(not parked) — see below.

---

## #1–#3 — llama_cpp performance-tuning sub-arc  (I3 · Major)

**Why I3:** this is the linchpin that unblocks **Slice 7** (flip the GPU backend
default `ollama → llama_cpp` and drop the Ollama daemon dependency) — the payoff
of the whole Phase-2 direct-loading arc (collapse the HTTP hop, per-model VRAM
control). Not `I4`: the cascade works today on Ollama and latency is a tiebreaker
metric, not a top one ([PRIORITIZATION.md]/metric-priorities). Not `I2`: it gates
a whole planned slice + the direct-loading thesis.

**The gap (Slice 2 / PR #95,
[FINDINGS-celery-phase2-parity.md](FINDINGS-celery-phase2-parity.md)):** functional
parity holds, but `llama_cpp` is **3.4× slower** than Ollama on GPU-heavy cases at
the current config (Case B: 110.4s vs 32.1s; Case C: 136.0s vs 40.5s). Same GGUF
blob is loaded on both backends, so the gap is **configuration**, not the model.

**Measurement harness (existing):** `scripts/parity_batch.py --backend
<ollama|llama_cpp>` writes `runs/parity-canvas-<backend>.json` with per-case wall
times. Re-run Case B/C after each tuning change; the bar is the Slice-2 criterion
(**within ±20% of Ollama's steady-state**). Needs the live GPU + CUDA
`llama-cpp-python` wheel — not a CI/eager change.

Current config in [cascade/llama_worker.py](../cascade/llama_worker.py)
(`make_llama_worker`): `n_gpu_layers=-1`, `n_ctx=8192`, `verbose=False`, no
`flash_attn`/`n_batch` tuning; `_generate` issues a fresh `create_chat_completion`
per call (re-prefills the system prompt every time).

Decomposed levers, in priority order:

### #1 · PT-1 — confirm full GPU offload  (I3 · S1)
The highest-suspicion, lowest-risk check first. Verify `n_gpu_layers=-1` actually
offloads **all** qwen14b layers (turn on the load log / count offloaded vs total).
If layers spill to CPU under VRAM pressure at `n_ctx=8192`, that alone could
explain most of the 3.4×. Pure diagnostic — no behavior change, fully reversible.

### #2 · PT-2 — context + attention config sweep  (I3 · S2)
Low-risk, reversible config, each lever measured independently:
- `n_ctx` `8192 → 4096` (Ollama sizes context dynamically to the prompt; a fixed
  8192 allocates a large KV cache = VRAM + per-token attention cost). Frees VRAM,
  which also helps PT-1.
- `flash_attn=True` (Ollama enables it by default for supported models).
- tune `n_batch` (prefill throughput; default 512).

The functional-parity gate (`parity_batch`) guards against any output change.

### #3 · PT-3 — KV-cache / system-prompt prefix reuse  (I3 · S3)
`_generate` re-prefills the `_SYSTEM` prompt on every call; Ollama's daemon keeps
the session/KV warm. Reuse the prefix KV across calls in the resident worker
(prompt caching). Higher severity: touches the call pattern and risks chat-template
/ correctness drift if prefix caching is mishandled — guarded by the parity gate.

**Decision gate (after PT-1..PT-3):** does steady-state Case B/C wall come within
±20% of Ollama? **Yes →** unblock Slice 7 (flip default). **Plateaus (e.g. ~1.5×) →**
explicit choice: accept the gap and flip anyway for the direct-loading benefits, or
keep Slice 7 parked + Ollama default. Either way record it in the parity findings.

---

## #4 — extract `_pick_first_verified` gating into a covered helper  (I2 · S2)
Reviewer idea from PR #103. The low_latency chord's gating *decision*
(cheapest-first / unavailable-skip / double-miss-cap) is product logic living in the
coverage-omitted celery substrate (`cascade/topologies_canvas.py`). It's
behaviorally tested by the 7 eager cases but not under the 100% gate. Extract the
pure decision into a non-omitted helper so the gate covers it. Minor impact (logic
*is* tested), small safe refactor.

## #5 — llama-cpp-python version check  (I2 · S2)
On `0.3.23`; check upstream for perf changes since the cu124 wheel was cut and bump
if it helps. Minor/maintenance; a bump can shift the CUDA wheel + runtime-DLL setup
([FINDINGS-celery-phase2-parity.md] side-finding), so measure before keeping.

---

## Blocked (dependency, not parked)

- **Slice 7 — default flip `ollama → llama_cpp` + Ollama deprecation**  (I3 · S2).
  A one-line `CONFIG.gpu_backend` default change + deprecation docs — *not* risky,
  so not an `S4` park. **Blocked on the PT decision gate above.** Re-enters the
  actionable order at `I3·S2` once perf comes within the bar (or the gate decides to
  flip despite a residual gap).

## User-owned (needs the hardware, not agent engineering work)

- **low_latency vs balanced wall-time comparison** — fill the TBD table in
  [FINDINGS-canvas-phase2-low-latency.md](FINDINGS-canvas-phase2-low-latency.md) by
  running `scripts/mesh_solve_canvas.py --topology {balanced,low_latency}` on the
  NPU + RTX + Redis box, then settle the Phase-0 decision gate for low_latency.
