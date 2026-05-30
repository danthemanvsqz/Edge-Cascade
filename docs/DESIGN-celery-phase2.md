# DESIGN — Celery Phase 2: direct-loading + per-model tasks + bare-metal workers + low_latency chord

> Status: **proposed design, pending review.** Same shape as
> [PLAN-canvas-phase1.md](PLAN-canvas-phase1.md). Companion to
> [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md) (the original Canvas
> direction), [CELERY-READINESS.md](CELERY-READINESS.md) (the six
> invariants), and [FINDINGS-canvas-phase1.md](FINDINGS-canvas-phase1.md)
> (Phase 1's live broker proof).

## North star

Phase 1 proved the Canvas substrate carries the cascade end-to-end on a
single box with Ollama-via-HTTP as the GPU tier. **Phase 2 collapses the
HTTP hop, breaks the GPU worker into per-model tasks, adds a dedicated
swap task for VRAM arbitration, lays out per-hardware worker pinning
(bare-metal), and lands the `low_latency` chord — all on the same
substrate, in slices small enough to bisect.**

The bundling reflects a real decision: bare-metal workers and the chord
share the *same* per-tier hardware-pinning design; building them
separately would mean refactoring the worker layout twice.

## Decisions locked (the six that shape everything below)

1. **Library: `llama-cpp-python`.** Same llama.cpp engine Ollama wraps;
   GGUF weights are already cached under `~/.ollama/models/blobs/`.
   Resident in worker init (~10s load), no re-download. Windows + CUDA
   support is mature.
2. **Per-model tasks: one task per active model.** Each task does ONE
   thing and only one thing: `generate_qwen14b_task`,
   `generate_qwen7b_task`, `image_sd15_task`, `draft_npu_task`,
   `cloud_generate_task`. The generic `generate_task` on `gpu` queue
   becomes a thin shim over the per-model versions.
3. **Swap arbiter: a dedicated Celery task.** `model.swap(name)` owns
   load/unload + resident-state tracking. Clients compose via
   `chain(model.swap.s("qwen14b"), generate_qwen14b.s(prompt))`. Swap
   sits on the same queue as the model it manages — natural ordering
   guarantee (FIFO within queue means swap completes before generate
   pops).
4. **Migration: parallel with feature flag** (`CASCADE_GPU_BACKEND=ollama|llama_cpp`).
   Both backends ship simultaneously; the flag picks at worker boot.
   Easier to bisect issues; brief code-path duplication accepted.
   `ollama` path stays canonical until parity proves out; flip default
   to `llama_cpp` only after a clean parity run (Phase 2 analog of
   Slice 4's findings).
5. **Bare-metal sequencing: direct loading FIRST, chord SECOND.** Lift
   Ollama → llama-cpp-python on the existing balanced topology, prove
   the substrate works direct, THEN add `low_latency` chord on top of
   the proven substrate. The chord's racing semantics build on a
   known-good per-tier worker.
6. **Testing: full integration with mocked broker.** Keep existing
   eager-mode unit tests for fast feedback + heavy mocking. ADD new
   integration tests using `pytest-celery`'s `celery_session_worker`
   fixture with an in-memory broker (`memory://`). MODEL inference is
   mocked at the `llama-cpp-python` boundary so no GPU is needed in CI.
   This setup would have caught all three PR #91 bugs (NotRegistered /
   gate divergence / cloud-queue deadlock).

## Architecture (post-Phase 2)

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │  agent / scripts/mesh_solve_canvas.py / cli.py                         │
 │       │                                                                │
 │       └─► solve_balanced_canvas(query, dsl, topology)                  │
 │                │                                                       │
 │                └─► Canvas signature (chain / chord / group)            │
 └────────────────────────────────────────────────────────────────────────┘
        │                                          ▲
        ▼                                          │ .get() at client
 Redis broker                                      │
        │                                          │
        ▼ queues route to per-hardware workers     │
 ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 │ Intel box        │    │ RTX 5070 Ti box  │    │ either box       │
 │  -Q npu          │    │  -Q gpu          │    │  -Q verify       │
 │                  │    │                  │    │                  │
 │  tasks resident: │    │  tasks resident: │    │  tasks resident: │
 │  • draft_npu     │    │  • model.swap    │    │  • verify_syntax │
 │  • route_npu     │    │  • generate_*    │    │  • verify_func   │
 │  • verify_syntax │    │    (per model)   │    │                  │
 │                  │    │  • image_sd15    │    │                  │
 │                  │    │  • model.status  │    │                  │
 │                  │    │                  │    │                  │
 │ OpenVINO         │    │ llama-cpp-python │    │ stdlib /         │
 │ qwen-coder-1.5b  │    │ + diffusers      │    │ subprocess       │
 └──────────────────┘    └──────────────────┘    └──────────────────┘
                                  │
                                  │ Resident _resident: dict[str, ModelHandle]
                                  ▼ tracks what's loaded RIGHT NOW per worker process
                          ┌──────────────────┐
                          │ {                │
                          │   "qwen14b": h1, │
                          │   "sd15": h2     │
                          │ }                │
                          └──────────────────┘
                                  ▲
                                  │ mutations only via model.swap
                                  │
                                ┌─┴───────────────────┐
                                │ model.swap(name)    │
                                │ • check resident    │
                                │ • compute footprint │
                                │ • evict if needed   │
                                │ • load              │
                                │ • update _resident  │
                                └─────────────────────┘
```

## Per-model task naming convention

Pattern: `{verb}_{model_slug}_task` for hardware-bound tasks; `{verb}_{tier}`
for tier-bound that doesn't fan out per model.

| Old (Phase 1) | New (Phase 2) | Queue | Notes |
|---|---|---|---|
| `tasks.route` | `route_npu_task` | `npu` | NPU is the only routing model. |
| `tasks.draft` | `draft_npu_task` | `npu` | NPU draft, 1.5B. |
| `tasks.generate` (Ollama 14b) | `generate_qwen14b_task` | `gpu` | Direct via llama-cpp. |
| — (new) | `generate_qwen7b_task` | `gpu` | Direct via llama-cpp, lighter. |
| — (new) | `generate_deepseek_r1_14b_task` | `gpu` | Reasoning model. |
| — (new, from existing image_server) | `image_sd15_task` | `gpu` | SDXL/SD1.5, talks to image_server.py over localhost HTTP. |
| `tasks.verify_functional` | `verify_functional_task` | `verify` | No model. |
| — (new) | `verify_syntax_task` | `verify` | The cheap syntax gate; lives here so the chain can pick gate per `dsl` arg. |
| `tasks.cloud_generate` | `cloud_generate_task` | `cloud` | Anthropic SDK; unchanged. |
| — (new) | `model.swap_task(name)` | `gpu` | Swap arbiter; lives on the same queue as the models it manages so FIFO orders swap-then-generate correctly. |
| — (new) | `model.status_task()` | `gpu` | Returns `{resident: [...], vram_free_mb: int}`. Read-only; for the dashboard. |

### Why "one task per model" matters

- **Single-responsibility.** A bug in qwen14b inference doesn't affect
  qwen7b's task body or test. Reviewable.
- **Routing clarity.** The Canvas chain dispatches the EXACT model it
  needs; no implicit "GPU worker has a default model" hidden state.
- **Future-proof for multi-GPU.** Per-model tasks can grow per-GPU
  queues (`gpu0`, `gpu1`) without touching the chain composition.
- **Testing.** Each task is mockable at the `llama-cpp-python` boundary
  independently.

## The swap task

```python
@app.task(name="model.swap", queue="gpu", bind=True)
def swap_task(self, name: str) -> dict:
    """Ensure `name` is the resident model on this worker's hardware.
    Idempotent: if already resident, returns immediately. Otherwise
    evicts current model(s) to fit VRAM budget + loads `name`.

    Returns {loaded: True, name, was_swap: bool, evicted: [...],
             vram_used_mb: int} on success, or
            {loaded: False, name, reason: str} on failure (model not
            found, OOM, etc.). NEVER raises -- the cascade treats
            unavailable models as a hand-off, not an error (charter
            inv. 5)."""
    ...

@app.task(name="model.status", queue="gpu", bind=True)
def status_task(self) -> dict:
    """Read-only snapshot for the dashboard. {resident: [...],
    vram_used_mb, vram_free_mb}."""
    ...
```

**Resident-state ownership.** `_resident: dict[str, ModelHandle]` lives
as a module-level singleton in the worker process (same pattern as
`_get_npu` cache from Slice 1, but mutable). Only `swap_task` writes to
it; generate tasks only read. Worker process boundary == resident state
boundary, so multi-box trivially isolates: each GPU box has its own
`_resident` dict.

**VRAM budget.** Each model knows its footprint via a small static map
(rounded up; conservative bias, like cloud_worker's `_PRICES`). Swap
evicts least-recently-used until `footprint(new) ≤ vram_free_mb`. If
new model alone exceeds total VRAM, return `{loaded: False, reason: "..."}`.

**Composition.** Clients chain swap + generate:

```python
chain(
    model.swap.s("qwen14b"),
    generate_qwen14b.s(prompt),  # ignores swap's result via .si() if needed
)
```

For the balanced topology, `_balanced_gpu_solve` becomes:

```python
self.replace(chain(
    model.swap.si("qwen14b"),     # immutable: ignore prior result
    gpu_solve_task.s(query=..., dsl=..., prior=...),
    _merge_gpu_into_env.s(env=env),
))
```

The cap-via-`gpu_solve_task.max_retries` invariant is unchanged.

## Migration: parallel with feature flag

`cascade/config.py` gains `gpu_backend: str = "ollama"` (env
`CASCADE_GPU_BACKEND=ollama|llama_cpp`). Default stays `ollama` until
the parity findings prove `llama_cpp` matches.

```python
# cascade/gpu_worker.py (refactored)
def make_gpu_worker() -> GPUWorker:
    if CONFIG.gpu_backend == "llama_cpp":
        from cascade.llama_worker import make_llama_worker
        return make_llama_worker(CONFIG.gpu_model)
    # Existing Ollama path
    ...

# cascade/llama_worker.py (new)
from llama_cpp import Llama

def make_llama_worker(model_id: str) -> GPUWorker:
    """Direct llama-cpp-python backend. Same GPUWorker shape
    (immutable dataclass, generate/available closures) so callers see
    no diff. Resident pipe lives in the closure, like make_npu_worker.

    Reads weights from the Ollama blob cache (`~/.ollama/models/blobs/`)
    by resolving the Ollama manifest -- one source of truth for which
    GGUF file backs which model id."""
    gguf_path = _resolve_ollama_blob(model_id)
    llm = Llama(model_path=gguf_path, n_gpu_layers=-1, ...)
    return GPUWorker(
        model=model_id,
        available=lambda: True,  # if init succeeded, we're available
        generate=partial(_llama_generate, llm),
    )
```

**Parity proof.** Same `scripts/parity_batch.py` framework as Slice 4 —
run the 3 cases with `CASCADE_GPU_BACKEND=ollama` then again with
`CASCADE_GPU_BACKEND=llama_cpp`, compare Outcomes. Pass criteria: same
`final_tier`, `resolved`, `repair_rounds`; wall time within ±20%; same
`.rec` deltas.

## `low_latency` chord (after direct loading lands)

```python
# cascade/topologies_canvas.py (Phase 2 addition)
def low_latency_signature(query, dsl=None):
    """NPU draft AND GPU generate race; first gate-passing answer wins.
    Discarded work is the cost of latency. Per DESIGN-celery-canvas.md
    section 'Topologies as Canvas'."""
    return chord(
        group(
            chain(draft_npu_task.s(query),
                  verify_syntax_task.s()),  # or functional if dsl
            chain(
                model.swap.si("qwen14b"),
                generate_qwen14b_task.s(query),
                verify_syntax_task.s(),
            ),
        ),
        pick_first_verified.s(),
    )
```

Where `pick_first_verified` is a chord callback that:
1. Takes a list of `[npu_result, gpu_result]`.
2. Returns the first that passed its gate.
3. If both passed, prefers NPU (cheaper).
4. If neither passed, falls through to the bounded repair loop (same
   gpu_solve_task as `balanced`).

**Cap invariant for low_latency.** The chord runs ONCE; if neither leg
passes its gate, the fallback signature continues to gpu_solve_task with
its `max_retries=CONFIG.repair_cap`. So the cap holds across the chord
too. Tested via the same eager-mode + live-broker pattern as Slice 3.

## Testing infrastructure upgrade

```python
# tests/conftest.py (new fixture)
import pytest

@pytest.fixture(scope="session")
def celery_session_app():
    """Real Celery app pointed at an in-memory broker for integration
    tests. No Redis needed; faster than testcontainers."""
    from cascade.celery_app import app
    app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
    )
    return app

# pytest-celery's celery_session_worker fixture auto-discovers tasks
# from the include list in celery_app; embedded worker handles dispatch.
```

```python
# tests/test_canvas_balanced_integration.py (new module)
"""Integration tests: dispatch the FULL chain through a real Celery
worker on an in-memory broker; mock only llama-cpp-python at its module
boundary. These tests would have caught the three PR #91 bugs.

Slower than unit tests (~5s each) but catches:
- Task registration / `include` issues
- Queue routing / chain composition
- Real .get() / result-backend semantics
- Worker-side imports / side effects
"""

def test_balanced_canvas_resolves_at_npu_on_live_broker(
    celery_session_app, celery_session_worker, mock_llama,
):
    ...
```

**The mock layer.** `mock_llama` fixture patches
`llama_cpp.Llama.create_chat_completion` to return canned text. The
TASK runs for real; only inference is fake. So we exercise:
- Real task registration
- Real broker dispatch
- Real chain composition with `self.replace()`
- Real swap task → resident-state tracking
- Real `.get()` result return

Existing unit tests stay (heavy mocks, fast feedback). New integration
tests are a small set (~10-15) covering the boundaries unit tests can't
reach.

## Slices

Each slice is one PR. Doc-only first, then code in dependency order.

### Slice 0 — this document

Doc-only PR. Reviewed for shape; locks the contracts the code slices
implement.

### Slice 1 — `llama-cpp-python` extra + `cascade/llama_worker.py`

Adds the new backend behind the feature flag. Existing Ollama path
unchanged.

- `pyproject.toml`: new `llama_cpp` extra (`llama-cpp-python>=0.3`).
- `cascade/llama_worker.py`: `make_llama_worker(model_id)` returns a
  `GPUWorker` (same shape as `make_gpu_worker` for drop-in).
- `cascade/config.py`: `gpu_backend: str = "ollama"` (env
  `CASCADE_GPU_BACKEND`).
- `cascade/gpu_worker.py`: `make_gpu_worker` branches on the flag.
- Unit tests: shape contracts on `make_llama_worker`, mocked at the
  `Llama` boundary.

**LOC budget:** ~120 prod + ~80 tests. **No new Celery tasks.**

### Slice 2 — Parity findings (Ollama vs llama_cpp)

Doc + reproducer.

- `scripts/parity_batch.py` extended to accept `--backend ollama|llama_cpp`.
- Live run on the user's hardware (same 3 cases as Phase 1 Slice 4).
- `docs/FINDINGS-celery-phase2-parity.md` with the comparison table.
- If parity ≥ pass criteria → flip default in a follow-up.
- If parity diverges → file blockers before Slice 3.

**LOC budget:** ~30 prod + findings doc.

### Slice 3 — Per-model task split + `model.swap` task

The biggest slice. Refactors `cascade/tasks.py` into per-model tasks +
introduces the swap arbiter.

- New module `cascade/model_swap.py` with `_resident: dict[str, ModelHandle]`
  + footprint map + LRU eviction policy.
- Per-model tasks:
  `generate_qwen14b_task`, `generate_qwen7b_task` (if both fit),
  `draft_npu_task`, `route_npu_task`, `verify_syntax_task`,
  `verify_functional_task`, `cloud_generate_task` (rename for
  consistency), `image_sd15_task` (talks to image_server.py).
- `model.swap_task`, `model.status_task`.
- Legacy `tasks.generate` becomes a thin shim that dispatches
  `generate_qwen14b_task` for backwards compat with the canvas chain.
- Update `cascade/topologies_canvas.py` chain steps to dispatch the new
  per-model tasks.
- Update existing tests; add swap-task tests.

**LOC budget:** ~300 prod + ~250 tests.

### Slice 4 — pytest-celery integration infrastructure

New test machinery + a small set of integration tests.

- `pyproject.toml`: `pytest-celery>=0.5` in dev group.
- `tests/conftest.py`: `celery_session_app` fixture + the mock_llama
  fixture pattern.
- `tests/test_canvas_balanced_integration.py`: 5-7 integration tests
  exercising the FULL chain through a real embedded worker + in-memory
  broker. Includes the three Phase 1 bugs as explicit regression cases.
- CI: integration tests run alongside unit tests (same
  `uv run pytest`); they add ~30-60s.

**LOC budget:** ~150 prod + ~250 tests.

### Slice 5 — Bare-metal worker layout docs + supervisor scripts

Operational, not architectural.

- `docs/BARE-METAL-CELERY.md`: how to start a worker on the Intel box
  vs RTX box. Windows host gotcha (WDAC blocks `celery.exe` → use
  `python -m celery`). Cross-box Redis (bind beyond localhost + auth).
- `scripts/start-worker-intel.ps1`, `scripts/start-worker-rtx.ps1`:
  per-box launch scripts honoring the queue subscription contracts.
- `cascade/celery_app.py`: `broker_url` accepts a host (config + env)
  for cross-box.
- No automated tests (manual ops). Slice docs claim "Phase 2 + cross-
  box" as the moment to test for real.

**LOC budget:** ~80 prod (scripts + config) + the doc.

### Slice 6 — `low_latency` chord

The headline composition Phase 2 was building toward.

- `cascade/topologies_canvas.py`: `low_latency_signature(query, dsl)`.
- `pick_first_verified` callback.
- Eager-mode tests on the new chord pattern.
- Live-broker integration test using the Slice 4 infra.
- Findings doc comparing `balanced` vs `low_latency` wall times on the
  user's hardware (Phase-0 decision-gate metric).

**LOC budget:** ~120 prod + ~150 tests + findings.

### Slice 7 — Default flip + Ollama deprecation (gated on Slice 2 findings)

If parity proves out:
- `CONFIG.gpu_backend` default flips `ollama` → `llama_cpp`.
- `docs/MIGRATION-llama-cpp.md`: brief note for anyone re-cloning.
- `cascade/gpu_worker.py` Ollama path stays for one release as a
  fallback, then removed in a Slice 8.

If parity diverges:
- Stay on Ollama; file the divergences for follow-up.

**LOC budget:** ~20 prod + docs.

## Out of scope (Phase 3+)

- Multi-GPU per box (`gpu0`, `gpu1` queues). Phase 2 assumes one GPU
  per box; per-model tasks already structure for fan-out.
- vllm backend. `llama-cpp-python` is the Phase 2 target; vllm
  considered if Slice 2's parity findings flag a real performance gap.
- LoRA / adapter swapping. Same `model.swap` pattern would extend; not
  in Phase 2.
- Auto-scaling worker count based on queue depth. Single
  worker-per-box is plenty for the cascade's single-prompt pattern.
- Distributed `.rec` aggregation. Each host writes its own
  `runs/<tier>.rec` in Phase 2 (multi-box artifact); aggregation lands
  when the dashboard pulls from all boxes.

## Charter invariants (D-table)

| | How Phase 2 honors it |
|---|---|
| **1** tier op is the only unit | Per-model tasks are tier ops at finer granularity; same boundary. |
| **2** op boundary = serializable data | Tasks still take/return JSON-clean dicts. Swap task returns a status dict, not a ModelHandle. |
| **3** composition = named topology | New `low_latency_signature` is one row of data, like `balanced`. |
| **4** cap = code, one constant | `gpu_solve_task.max_retries = CONFIG.repair_cap` unchanged. Swap doesn't bypass it. |
| **5** `.rec` at op boundary | Each per-model task wraps its body in `@recorded(_REC)` for the matching lane. Swap writes to `runs/edge-swap.rec` (new lane). |
| **6** no streaming dependence | Tasks return final results. llama-cpp internal streaming is opaque to the task boundary. |

## Risks + mitigations

| Risk | Slice | Mitigation |
|---|---|---|
| `llama-cpp-python` GGUF path differs from Ollama's quantization → quality regression | Slice 1-2 | Use Ollama's own blob cache via `_resolve_ollama_blob`; load the SAME file. Parity batch (Slice 2) catches quality drift. |
| Worker boot time blows past acceptable (10s × N models) | Slice 3 | `model.swap` is on-demand; worker only loads what's needed. Status task surfaces resident set for debugging. |
| `pytest-celery` + embedded worker is finicky on Windows | Slice 4 | Fallback: keep eager mode for the bulk; integration tests use `task_always_eager=False` + in-memory broker (not full pytest-celery) if needed. |
| Cross-box Redis auth not documented | Slice 5 | Slice 5's findings doc + supervisor scripts make it explicit. |
| `low_latency` chord wins for trivial prompts (NPU PASSes first) become slower than `balanced` because GPU spun up unnecessarily | Slice 6 | Chord is a topology choice, not a default. Findings doc compares wall times; user picks per workload. |
| The `repair_rounds` off-by-one between pipe/canvas (filed at Slice 4) compounds with new chord semantics | Slice 6 | Align both paths in Slice 6 before adding the chord; small change either side. |

## Phase 2 scorecard (filled as slices land)

| Slice | Title | PR | Status |
|---|---|---|---|
| 0 | This design doc | this PR | pending review |
| 1 | llama-cpp backend behind feature flag | — | not started |
| 2 | Parity findings (Ollama vs llama_cpp) | — | not started |
| 3 | Per-model tasks + `model.swap` | — | not started |
| 4 | pytest-celery integration infra | — | not started |
| 5 | Bare-metal layout docs + launch scripts | this PR | pending review |
| 6 | `low_latency` chord (6a align + 6b chord) | #102 + this PR | pending review |
| 7 | Default flip + Ollama deprecation | — | not started |

## After Phase 2

Phase 3 candidates (per `DESIGN-celery-canvas.md`): multi-GPU per box,
auto-scaling worker count, distributed `.rec` aggregation, additional
topologies (e.g. `consensus` running 3 generators voting through
verify). Phase 2 lays the per-model + per-hardware seams those build on.
