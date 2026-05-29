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

## Results

**Pending — run on the user's hardware after the CUDA wheel install.**
Methodology and reproducer committed in this slice so the runner doesn't
re-derive the protocol.

### Case A — NPU gate PASS

| Backend | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|---|
| `ollama` | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| `llama_cpp` | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Case B — GPU first attempt PASS

| Backend | Wall (s) | final_tier | repair_rounds | difficulty | `edge-npu.rec` Δ | `edge-gpu.rec` Δ | `edge-verify.rec` Δ |
|---|---|---|---|---|---|---|---|
| `ollama` | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| `llama_cpp` | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Case C — Cap → Tier-3 (cloud disabled, contradictory DSL)

| Backend | Wall (s) | final_tier | repair_rounds | `edge-gpu.rec` Δ | `edge-cloud.rec` Δ |
|---|---|---|---|---|---|
| `ollama` | TBD | TBD | TBD | TBD | 0 (expected) |
| `llama_cpp` | TBD | TBD | TBD | TBD | 0 (expected) |

## Verdict

**Pending live results.** If the tables fill in matching `final_tier`,
`resolved`, `capped`, `repair_rounds` across both backends with wall
times within ±20% and matching tier attribution in `.rec` deltas →
file a Slice-7 follow-up to flip `CONFIG.gpu_backend` default to
`llama_cpp`. If anything diverges, file the divergence as a Slice-3
blocker (per-model tasks + `model.swap` ride on the assumption that
direct loading works at parity).

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
