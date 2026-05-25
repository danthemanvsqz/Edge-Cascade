# FINDINGS — Local LLM VRAM Load vs. Capability (12 GB RTX 5070 Ti Laptop)

**Date:** 2026-05-23 · **Hardware:** RTX 5070 Ti Laptop, 12 GB · **Substrate:** Ollama (local, $0)

**Evidence (do not merge / do not delete):** `experiment/llm-vram-capability-2026-05-23` @ `399d524` — raw driver script + benchmark JSON + the experimental `image_worker.py` change live there.

## TL;DR

- **Capability — not throughput or VRAM — is the lever for hard coding tasks.** A
  faster model that fits the card easily is worthless if it can't produce correct code.
- **Quality ranking on the dijkstra ("shortest-path") gate** (all local, $0):
  **`deepseek-r1:14b` (2/2 fresh) > `qwen2.5-coder:14b` (3/3, 1 repair) ≫
  `qwen2.5-coder:7b` = `deepseek-coder:6.7b` (0/3, fail).**
- **Scoring priority:** quality + cost first; **latency is not a priority metric.**
- `nvidia-smi` "memory.used" is **caching-muddied** and must not be read as load — trust
  the GPU/CPU split and the task outcome.

---

## 1. Motivation

Hypothesis under test: *"the 14b coder underperforms because it's VRAM-starved on 12 GB."*
True at large context — but the investigation showed VRAM/speed are the wrong things to
optimize; capability is.

## 2. VRAM load analysis (and why it's not the answer)

Context-scaling sweep, model loaded fresh per point, default-token generation:

| Model | num_ctx | Processor split | tok/s |
|---|---:|---|---:|
| qwen2.5-coder:14b | 4,096 | 100% GPU | 48.6 |
| qwen2.5-coder:14b | 8,192 | 100% GPU | 47.4 |
| qwen2.5-coder:14b | 12,288 | **5% CPU** / 95% GPU | 39.4 |
| qwen2.5-coder:14b | 16,384 | 14% CPU / 86% GPU | 24.3 |
| qwen2.5-coder:14b | 32,768 | 38% CPU / 62% GPU | 11.9 |
| qwen2.5-coder:7b | 8,192 | 100% GPU | 102.4 |
| qwen2.5-coder:7b | 32,768 (native max) | 100% GPU | 99.4 |

- **14b cliff is between 8k and 12k:** at 12k the working set hits the 12 GB ceiling and
  spills to CPU. **Max fully-on-GPU context for the 14b ≈ 8k.** Small spills bite hard:
  5% CPU = −17% throughput, 14% CPU = −50%, 38% CPU = −75%. CPU offload is a serial
  bottleneck, not a graceful taper.
- **7b never spills** across its native 32k range (~8.2 GB, ~5.4 GB free, ~100 tok/s flat).
- **KV-cache cost/token:** 14b ≈ 0.18 MB, 7b ≈ 0.06 MB (~3× lighter). That KV slope, not
  weight size alone, is why 7b holds far more context on this card.

**Caveat (important):** `nvidia-smi memory.used` includes reserved-but-free allocator
pools (PyTorch/Ollama), so it overstates true need. It is **not** a clean load metric.
The honest pressure signal is Ollama's GPU/CPU split; the honest *answer* is the benchmark.

## 3. Capability benchmark — the decisive test

`scripts/model_bench.py` on the dijkstra gate: generate → deterministic functional-verify
→ bounded repair (cap 2). `fresh` = correct with **no** repair (cleanest quality signal);
`resolved` = passed within the cap; `capped` = failed.

| Model | runs | fresh | resolved | capped | tok/s | notes |
|---|---:|---:|---:|---:|---:|---|
| **deepseek-r1:14b** | 2 | **2/2** | 2/2 | 0 | 41.2 | reasoning model; correct first-try |
| qwen2.5-coder:14b | 3 | 0/3 | **3/3** | 0 | 45.3 | resolves in 1 repair round |
| qwen2.5-coder:7b | 3 | 0/3 | 0/3 | **3** | 87.1 | fast, never resolves |
| deepseek-coder:6.7b | 3 | 0/3 | 0/3 | **3** | 94.0 | fastest, never resolves |

- Only **`deepseek-r1:14b`** produces correct dijkstra with **zero repair** — its `<think>`
  trace catches the sink-node edge case (uninitialised distance for a node that appears
  only as a neighbour) that every coder model trips on.
- `qwen2.5-coder:14b` never nails it fresh but the repair loop fixes it **every time, in
  one round**.
- The two fastest models (`7b`, `6.7b`) **cannot solve it at all**, even with two repairs.
  Fast, free, and wrong.

## 4. Quality ranking (scored by priority: quality → cost → [latency: ignored])

| Rank | Model | Quality | Cost | tok/s | latency |
|---|---|---|---:|---:|---:|
| 1 | deepseek-r1:14b | 2/2 fresh (no repair) | $0 | 41.2 | ~154 s/run 🐌 |
| 2 | qwen2.5-coder:14b | 3/3 resolved (1 repair) | $0 | 45.3 | ~10 s/gen |
| 3 | qwen2.5-coder:7b | 0/3 (fails) | $0 | 87.1 | fast |
| 3 | deepseek-coder:6.7b | 0/3 (fails) | $0 | 94.0 | fastest |

## 5. Implications for the cascade

A quality-first, $0-local difficulty ladder:

- **Easy / large-context / drafts →** `qwen2.5-coder:7b` (capable enough there; 32k headroom;
  the only model that co-resides with the SD 1.5 image server — 7b ~8 GB + SD1.5 ~2.8 GB fit
  together; a 14b cannot).
- **Standard hard local →** `qwen2.5-coder:14b` (resolves with one repair; faster than r1).
- **Hardest reasoning — free backstop *before* paid cloud →** `deepseek-r1:14b` (the only local
  model that solves the hard gate; exhaust it before spending on Tier-3/4).

**VRAM constraints on the two 14b-class rungs:** ~8k fully-on-GPU ceiling, and they cannot
share the card with imaging. r1's long reasoning traces add KV pressure, so it's a
bounded-context tool.

## Action items

- **AI-1 — Re-land the model-agnostic image loader.** The `AutoPipelineForText2Image`
  change (lets `CASCADE_IMAGE_MODEL` point at SD1.5 ~2.8 GB vs SDXL ~11.6 GB) currently
  lives only on the evidence branch. Re-implement via a clean PR. *(evidence:
  `cascade/image_worker.py` @ `399d524`)* **DONE (2026-05-24):** re-landed in
  `cascade/image_worker.py::_load_pipe` with an fp16-variant fallback (SD1.5 ships no
  fp16 variant). `CASCADE_IMAGE_MODEL` already existed in `config.py`; no config change.
- **AI-2 — Set the GPU tier deliberately.** Do NOT promote 7b (fails the hard gate). Keep
  `qwen2.5-coder:14b` as Tier-2; add `deepseek-r1:14b` as the reasoning rung. **DONE
  (2026-05-24):** `gpu_model` stays the 14b coder (7b never promoted) and
  `config.gpu_reasoning_model` (`CASCADE_GPU_REASONING_MODEL`, default `deepseek-r1:14b`)
  now declares the reasoning rung. Config seam only — the coder→r1 fallback *flow* is
  AI-3, deferred until AI-4 proves it earns its keep.
- **AI-3 — Spike the coder→r1 fallback.** Gate-failure-triggered cross-model retry before
  paid cloud. Pending AI-4.
- **AI-4 — Run the Bayesian-MC complementarity experiment.** Paired trials across several
  task types, ~30 runs/cell; estimate `P(r1 resolves | coder capped)` with a credible
  interval. Own evidence branch.
- **AI-5 — Drop `nvidia-smi memory.used` as a load metric** (caching-muddied); use the
  GPU/CPU split + task outcome. **DONE (2026-05-24):** verified no live consumer — the
  cascade's only VRAM signal is `mcp_servers/gpu.py::_vram`, which reads Ollama
  `/api/ps` `size_vram` (the GPU/CPU split), not `nvidia-smi`. `memory.used` only ever
  lived in the throwaway sweep driver on the evidence branch (§2/§7); nothing to remove.

## 6. Method notes & caveats

- **`vram_used` caching confound** (see §2) — outcome-based metrics only.
- **r1 run** used `--max-tokens 6000` but Ollama defaulted to a 4k context window; r1 still
  passed, so there is **no truncation confound** (truncation only causes false *failures*).
  Its code extracted cleanly from under `<think>` in the verify gate.
- Benchmarks use Ollama's default (stochastic) temperature and repair cap 2; `0/3 capped`
  across 3 stochastic runs is a strong capability signal, not variance.
- **`CASCADE_GPU_MODEL` selection verified** end-to-end over the real `edge-gpu` MCP server:
  setting it to `qwen2.5-coder:7b` makes Tier-2 report and run 7b. (Persisting via `.env`
  is the user's lane — `.env` writes are sandbox-gated.)

## 7. Reproduce

```powershell
# VRAM/context sweep: load each (model, num_ctx), read ollama ps + nvidia-smi
# Capability benchmark (the decisive one):
uv run python scripts/model_bench.py --runs 3 --no-pull `
  --models qwen2.5-coder:14b,qwen2.5-coder:7b,deepseek-coder:6.7b
# Reasoning model with room to think:
uv run python scripts/model_bench.py --runs 2 --no-pull --max-tokens 6000 `
  --models deepseek-r1:14b
```
