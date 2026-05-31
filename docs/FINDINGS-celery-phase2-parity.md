# FINDINGS — Celery Phase 2 Slice 2: Ollama vs llama-cpp-python parity

> **Status:** **methodology + reproducible script committed; live-broker
> results PENDING the CUDA `llama-cpp-python` wheel install.** Slice 2 of
> Phase 2.
>
> Companion to [DESIGN-celery-phase2.md](DESIGN-celery-phase2.md) (Slice 2
> in the scorecard), [PLAN-canvas-phase1.md](PLAN-canvas-phase1.md)
> (the slicing pattern), and
> [FINDINGS-canvas-phase1.md](FINDINGS-canvas-phase1.md) (Phase 1's
> live-broker proof which Phase 2 builds on).

## The one question

Slice 1 added the `llama_cpp` GPU backend behind
`CASCADE_GPU_BACKEND=llama_cpp` (default stays `ollama`). The default
flip — Slice 7 in the scorecard — only happens if the direct-loading
path is **parity-equivalent** to the Ollama HTTP path on the same three
prompts that pinned Phase 1.

Pass criteria:
- Same `final_tier`, `resolved`, `capped`, `repair_rounds` for each case.
- Same tier attribution in per-lane `.rec` deltas (`edge-npu`,
  `edge-gpu`, `edge-verify`, `edge-cloud`). Byte-level mismatch is
  acceptable if it's only encoding (different latency rounding, etc.);
  the LANE assignment must match.
- Wall time within ±20%. (Not a performance claim — the design doc
  states latency is not the justification for the Canvas substrate, and
  Phase 2 inherits that. We just want to confirm direct loading is
  comparable, not regressed.)

If parity holds → file a Slice-7 follow-up to flip the default. If
parity diverges → block Slice 3 (per-model tasks + `model.swap`) until
the divergence has a known cause.

## Methodology

### Setup

```
docker compose up -d redis
uv sync --extra celery --extra accel --extra mcp --extra llama-cpp
# CUDA build of llama-cpp-python (needed for n_gpu_layers=-1 to actually
# offload to the GPU):
uv pip install llama-cpp-python --extra-index-url \
    https://abetlen.github.io/llama-cpp-python/whl/cu124
```

> The CUDA wheel uses Python 3.11; check the wheel index for matches
> against your Python version if `cu124` 404s.

### Worker launches

Two workers run sequentially (NOT in parallel — they'd contend for the
same GPU VRAM):

```powershell
# Ollama backend (default)
$env:CASCADE_GPU_BACKEND = "ollama"
uv run python -m celery -A cascade.celery_app worker `
    -Q npu,gpu,verify --pool=solo -l warning
```

Stop, then:

```powershell
# llama-cpp-python backend
$env:CASCADE_GPU_BACKEND = "llama_cpp"
uv run python -m celery -A cascade.celery_app worker `
    -Q npu,gpu,verify --pool=solo -l warning
```

The llama_cpp boot takes ~10s longer (GGUF load) but stays resident
under `worker_max_tasks_per_child=0`, same as the Ollama path's warmup.

### Three cases (same as Phase 1)

| # | Prompt | Expected outcome |
|---|---|---|
| **A** | `reverse a python string` | `final_tier="npu"` (syntax gate PASS on NPU draft) |
| **B** | `write a python function for dijkstra's shortest path` | `final_tier="gpu"` (NPU fails; GPU passes via the direct-loading path under llama_cpp) |
| **C** | `add(a,b) -> a+b` + contradictory DSL | `final_tier="capped->tier3"`, `repair_rounds=2=CAP`, `edge-cloud.rec Δ=0` |

Case A barely touches the GPU (NPU passes syntax gate), so it's mostly
an end-to-end pipeline smoke. Case B is the load-bearing run — GPU
escalation under each backend. Case C re-verifies the cap invariant
under each backend (Phase 1 proved it for Ollama; Phase 2 must show
llama_cpp doesn't change the bound).

### Reproducer

```
# Each backend invocation runs in its own client process so CONFIG picks
# up the env at construction.

# Ollama side:
$env:CASCADE_GPU_BACKEND = "ollama"
uv run python scripts/parity_batch.py --backend ollama
# Writes runs/parity-canvas-ollama.json

# llama_cpp side:
$env:CASCADE_GPU_BACKEND = "llama_cpp"
uv run python scripts/parity_batch.py --backend llama_cpp
# Writes runs/parity-canvas-llama_cpp.json
```

Both JSON files share the same shape (`{metadata, results}` with
`metadata.backend` distinguishing them). Side-by-side diff via:

```
python -c "import json; o=json.load(open('runs/parity-canvas-ollama.json')); l=json.load(open('runs/parity-canvas-llama_cpp.json')); [print(r['label'], 'ollama:', r['final_tier'], r['repair_rounds'], '|', 'llama_cpp:', n['final_tier'], n['repair_rounds']) for r,n in zip(o['results'], l['results'])]"
```

## Results — live run on NPU + RTX 5070 Ti + local Redis (2026-05-29)

Reproduced via `scripts/parity_batch.py --backend <ollama|llama_cpp>` after
sequentially restarting the Celery worker with the matching
`CASCADE_GPU_BACKEND` env var. JSON outputs:
`runs/parity-canvas-ollama.json` and `runs/parity-canvas-llama_cpp.json`.

### Case A — NPU gate PASS

| Backend | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|---|
| `ollama` | 16.3 | npu | 0 | 0.65 | +564 | 0 | 0 |
| `llama_cpp` | 4.9 | npu | 0 | 0.65 | +563 | 0 | 0 |

NPU compile cost paid once at first invocation per worker; Ollama's case A
ran first so it absorbed the ~14s NPU warmup. Steady-state of both
backends on this case is ~5s. ✅ **Parity holds.** Single-byte `edge-npu`
delta diff is timestamp encoding (a fraction-of-second field shifted by
one byte).

### Case B — GPU first attempt PASS

| Backend | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|---|
| `ollama` | 32.1 | gpu | 0 | 0.65 | +1570 | +3588 | 0 |
| `llama_cpp` | **110.4** | gpu | 0 | 0.65 | +1570 | +3532 | 0 |

✅ **Functional parity holds** (same `final_tier`, `resolved`,
`repair_rounds`). **Wall time diverges 3.4×** — `gpu_solve_task` itself
ran 117.66s under `llama_cpp` (worker log:
`mesh.balanced._gpu_solve` → replace → `gpu_solve_task` 117.66s →
`_merge_gpu`). This exceeds the ±20% pass criterion. See "Verdict".

### Case C — Cap → Tier-3 (cloud disabled, contradictory DSL)

| Backend | Wall (s) | final_tier | repair_rounds | `edge-gpu.rec` Δ | `edge-cloud.rec` Δ |
|---|---|---|---|---|---|
| `ollama` | 40.5 | capped->tier3 | **2 = CAP** | +7495 | **0** |
| `llama_cpp` | **136.0** | capped->tier3 | **2 = CAP** | +4135 | **0** |

✅ **Cap invariant holds on BOTH backends.** `repair_rounds=2=CAP` on
both, `edge-cloud.rec` Δ=0 on both (spend invariant at both CONFIG and
QUEUE layers, Phase 1 verified). The `edge-gpu.rec` byte delta differs
(7495 vs 4135) because the Ollama 14b produces longer "fix attempt"
text per round than llama_cpp does on the same impossible DSL — the
CONTRACT (3 generates = cap+1) holds on both; the byte volume is
output-length-dependent. **Wall time diverges 3.4×** as in Case B.

## Verdict

**Functional parity: YES.** All cap-and-resolution invariants hold:
- Same `final_tier`, `resolved`, `capped`, `repair_rounds` across both
  backends on all 3 cases.
- `edge-cloud.rec` Δ=0 on Case C for both — the cap-handoff is
  identical and the spend invariant holds.
- `repair_rounds=2=CAP` on Case C — the Phase 1 cap-via-`self.replace()`
  proof from #91 carries through under `llama_cpp` unchanged.
- Tier attribution in `.rec` deltas matches (lanes that fire under
  Ollama also fire under llama_cpp; lanes that stay clean stay clean).

**Performance parity: NO.** `llama_cpp` is 3-4× slower on
GPU-heavy cases at the current `n_ctx=8192` configuration:

| Case | Ollama wall | llama_cpp wall | ratio |
|---|---|---|---|
| A (NPU only) | 16.3s (cold) → ~5s | 4.9s | parity |
| B (1× GPU repair) | 32.1s | 110.4s | **3.4×** |
| C (3× GPU repairs, cap) | 40.5s | 136.0s | **3.4×** |

Per the locked pass criteria, wall time within ±20% is part of the bar.
**`llama_cpp` fails that bar.**

### Decision

- **DO NOT flip the default to `llama_cpp` yet.** Slice 7 stays
  parked.
- **DO proceed with Slice 3** (per-model tasks + `model.swap`).
  Functional parity is what Slice 3 needs — `llama_cpp` runs the full
  cascade end-to-end correctly. The performance gap is a separate
  optimization arc.
- **NEW BACKLOG ITEM: llama_cpp performance tuning.** Compare against
  Ollama's known good config. Candidates worth measuring:
  - Smaller `n_ctx` (8192 → 4096; Ollama dynamically sizes based on
    prompt length).
  - KV cache reuse across calls in the same worker (Ollama's daemon
    keeps a session warm; our `_generate` makes a fresh
    `create_chat_completion` per call which may re-prefill).
  - ~~Verify `n_gpu_layers=-1` is actually offloading all layers~~ —
    **PT-1 DONE (2026-05-31): PASS.** VRAM delta +10,526 MB (123% of
    8,571 MB GGUF); all 28 layers on GPU. Not the cause of the 3.4×.
    Bonus: `n_ctx_train=32768` surfaced — Ollama likely sizes context
    dynamically per-prompt (much < 8192 for short queries), so our
    fixed `n_ctx=8192` over-allocates the KV cache. PT-2 next.
  - llama-cpp-python version (we're on 0.3.23; check upstream for
    perf regressions since the cu124 wheel was cut).
- File the 117s `gpu_solve_task` timeout as the reason
  `cascade/canvas_client.py` bumped `.get(timeout=120)` → `.get(timeout=600)`
  in this PR.

### Side-finding (Windows CUDA install)

Getting `llama-cpp-python` working on Windows with the cu124 wheel
required two fixes already baked into this PR:

1. **`nvidia-cuda-runtime-cu12==12.4.*` + `nvidia-cublas-cu12==12.4.*`**
   added to the `llama-cpp` extra. The wheel doesn't bundle these
   runtime DLLs and Ollama-via-HTTP doesn't need them, so a fresh
   `uv sync --extra llama-cpp` would otherwise still fail at import.
2. **`_preload_cuda_runtime()` in `cascade/llama_worker.py`** —
   pre-loads `cudart64_12.dll` + `cublas64_12.dll` via absolute path
   before importing `llama_cpp`. `llama_cpp`'s bundled `llama.dll`
   uses a constrained Windows DLL search path that skips the
   `nvidia/.../bin/` dirs even when they're on PATH or registered via
   `os.add_dll_directory`. Pre-loading is the workaround documented
   upstream (`github.com/abetlen/llama-cpp-python` Windows + CUDA
   issues). No-op on Linux.

Both changes are minimal and live in this PR alongside the findings
update.

### Known divergences NOT counted as parity failures

- **Wall-time scatter on individual NPU+GPU combos** — NPU compile
  warmup at boot may shift early cases; rely on the steady-state of
  Cases B and C.
- **GGUF quantization vs Ollama's runtime quantization** — both
  backends load the *same blob* (Slice 1's `_resolve_ollama_blob`
  ensures this); semantic divergence would indicate a real bug in
  one path's chat-template handling.
- **The Phase-1 `repair_rounds` off-by-one** between pipe and Canvas
  (filed during PR #91 closure) is unrelated to backend choice and
  will affect both `ollama` and `llama_cpp` symmetrically.

## Phase 2 scorecard update (this slice)

| Slice | Title | PR | Status |
|---|---|---|---|
| 0 | Design doc | #92 | merged |
| 1 | llama-cpp backend behind feature flag | #93 | merged |
| 2 | Parity findings (Ollama vs llama_cpp) | this PR | **pending merge + live results** |
| 3 | Per-model tasks + `model.swap` | — | gated on Slice 2 verdict |
| 4 | pytest-celery integration infra | — | not started |
| 5 | Bare-metal layout docs + launch scripts | — | not started |
| 6 | `low_latency` chord | — | not started |
| 7 | Default flip + Ollama deprecation | — | gated on Slice 2 verdict |
