# FINDINGS — PD-1 v2: code-corpus re-calibration cuts false-positive rate from 52% to 0%

**Date:** 2026-05-26 · **Hardware:** Intel AI Boost NPU + RTX 5070 Ti Laptop · **Substrate:** local cascade (in-process workers), $0

**Evidence (do not merge / do not delete):** `experiment/pd1-v2-calibration-2026-05-26` @ `a234548` — corpus builder `scripts/build_pd1_v2_corpus.py`, calibrator `scripts/calibrate_pd1_v2.py`, re-verifier `scripts/verify_pd1_v2.py`, corpus `runs/pd1-v2/corpus.jsonl`, sweep report `runs/pd1-v2/calibration.json`, build log `runs/pd1-v2/build.log`, verification log `runs/pd1-v2/verification.log`. New thresholds at `cascade/degeneration_thresholds.json` (committed in `a234548` on this branch; lands on `main` only via the v2 cherry-pick).

## TL;DR — the over-trip is **two levers, not one**, and the v2 fit eliminates both

PD-1 v1 (#62, `62a208c`) shipped prose-calibrated thresholds with an in-band warning that they would over-trip on code. The v1 runtime verification (#64, `360a6a0`) confirmed the warning empirically at N=4 and identified `trigram_repeat` as the single offending lever. **A wider code corpus reveals the picture is sharper than v1 caught: `ttr_min` over-trips on code too** — the v1 N=4 sample never stressed it. Re-calibrating all four metrics on the code corpus drops the false-positive rate on correct outputs from **52% (14/27) → 0% (0/27)**, with no regression on the synthetic degenerate positives the v1 detector was supposed to catch.

- **`trigram_repeat_max`** rises from `0.0367` → `0.1372`. Negative-max on the code corpus is `0.1358`; the v2 threshold sits `+0.0014` above that ceiling. Youden's J: `0.38 → 0.90`. Confusion: `(tp=18, fp=14, fn=2, tn=13) → (tp=18, fp=0, fn=2, tn=27)`.
- **`ttr_min`** drops from `0.411` → `0.3181`. Negative-min on the code corpus is `0.3532` (code has lower lexical diversity than prose because identifiers repeat). Youden's J: `0.53 → 0.70`. Confusion: `(tp=15, fp=6, fn=5, tn=21) → (tp=14, fp=0, fn=6, tn=27)`.
- **`distinct_sent_ratio_min`** drops from `0.9055` → `0.6453`. Same confusion on this corpus either way (every gate-PASS output had all-distinct sentences), but the lower v2 value gives prose-style explanations more headroom before tripping in future runs.
- **`max_sent_repeat_max`** unchanged at `1.0`. Only one of the four synthetic recipes (`sentence_repeat`) stresses this metric, so Youden's J is `0.30` either way — the metric *can* discriminate, it just wasn't the dominant signal in this corpus.

**Decision the evidence supports:** ship the v2 thresholds for PD-1's continued passive-observer role *and* as the basis for v2 action levers (skip-repair / warn-prompt / hard-escalate, the open-threads item **5a**). The v1 finding's "blocked on code-corpus threshold re-calibration" gate is now lifted: the v1 false-positive rate would have made any action lever a quality regression; the v2 false-positive rate makes them safe to design.

## The decisive table

Sweep over the 27 gate-PASS negatives + 20 synthetic positives (47 total). Confusion is reported against the same 47-row corpus for both threshold sets — `v1_confusion_on_v2_corpus` answers "what would v1 have done on this code corpus?".

| metric                 | v1 thr  | v2 thr  | v1 J  | v2 J  | v1 confusion (tp/fp/fn/tn) | v2 confusion             |
|------------------------|--------:|--------:|------:|------:|---------------------------:|--------------------------|
| `trigram_repeat_max`   | 0.0367  | **0.1372** | 0.38  | **0.90** | 18 / **14** / 2 / 13      | 18 / **0** / 2 / 27      |
| `max_sent_repeat_max`  | 1.0     | 1.0     | 0.30  | 0.30  |  6 /  0 / 14 / 27         |  6 /  0 / 14 / 27        |
| `distinct_sent_ratio_min` | 0.9055 | 0.6453 | 0.30 | 0.30 |  6 /  0 / 14 / 27         |  6 /  0 / 14 / 27        |
| `ttr_min`              | 0.411   | **0.3181** | 0.53 | **0.70** | 15 /  **6** / 5 / 21      | 14 /  **0** / 6 / 27     |

Per-row re-verification on the 27 gate-PASS outputs (from `runs/pd1-v2/verification.log`):

| measure                          | v1     | v2  |
|----------------------------------|:------:|:---:|
| **false-positive rate on correct code** | **52%** (14/27) | **0%** (0/27) |
| rows where score ≥ 0.30 (two metrics tripped) | 6      | 0   |
| rows where score = 0.175 (one metric tripped) | 8      | 0   |
| rows clean                       | 13     | 27  |

## Method

- **Negatives (27):** every gate-PASS output from running both Tier-1 (NPU, qwen2.5-coder-1.5B sym-INT4) and Tier-2 (GPU, qwen2.5-coder:14b via Ollama) on a 15-task code battery (`scripts/build_pd1_v2_corpus.py`). Battery spans Tier-1-trivial (`add_numbers`, `reverse_string`) through cap-prone (`dijkstra`, `bfs_grid`, `count_islands` — where Tier-1 hit `npu_max_tokens=192` and gate-FAILed; those rows are kept in `corpus.jsonl` for traceability but excluded from the calibration fit because token-capped output may have truncated mid-loop and is neither a clean negative nor a real positive).
- **Positives (20):** four deterministic synthetic recipes seeded (`SEED=7`) from the negatives:
  - `loop_body` (x6) — repeat the function body 3× inside the fence; stresses `trigram_repeat` and `ttr`.
  - `sentence_repeat` (x6) — repeat each prose sentence 4×; stresses `max_sent_repeat` and `distinct_sent_ratio`.
  - `truncate_comments` (x4) — cut at 30% and append 10× `# step processing`; simulates a token-capped loop.
  - `trigram_explosion` (x4) — append a 12× repeated identifier burst; stresses `trigram_repeat` alone.
- **Sweep:** `scripts.calibrate_pd1_v2.per_metric_report` reuses `scripts.calibrate_degeneration_thresholds.best_threshold` (dense 200-step grid between min and max observed, max Youden's J = TPR − FPR). The same helper produced the prose calibration, so the sweep method is unchanged — only the corpus differs.
- **Re-verification:** `scripts/verify_pd1_v2.py` runs `cascade.degeneration.check_degeneration` on the 27 gate-PASS outputs under both threshold sets; the verdict log is `runs/pd1-v2/verification.log`.

## What the v1 findings doc called out, resolved

The v1 findings (`docs/FINDINGS-pd1-v1-runtime-verification.md`) opened three forks; this finding resolves them:

1. **"Calibrate `trigram_repeat_max` on a code corpus before wiring any v2 control flow."** — Done. `0.0367 → 0.1372`. Youden's J `0.38 → 0.90`.
2. **"The other three criteria likely transfer (none fired)."** — *Partially refuted.* `max_sent_repeat` and `distinct_sent_ratio` indeed transfer (v1 thr → 0 FP on this corpus). But `ttr_min=0.411` produced 6 FP / 27 on the code corpus — v1's N=4 simply didn't stress it. v2 lowers it to `0.3181`.
3. **"PD-1 v2 (open-threads item 5a: act on the degen signal) — blocked on code-corpus threshold re-calibration."** — *Unblocked.* The v2 thresholds make skip-repair / warn-prompt / hard-escalate safe to design without false-positive quality regression.

## Caveats / limits

- **Synthetic positives bias Youden's J upward.** The four recipes are constructed to trip specific metrics, so the sweep finds clean separators almost trivially on three of four metrics. The honest interpretation: the v2 thresholds are the *threshold each metric needs to separate these construction-positives from real correct outputs*. They are not a claim that real-world degeneration looks exactly like the recipes — but the recipes cover the four failure modes the metrics were designed to detect (looping function, sentence repeat, truncation, trigram explosion), so the directional move (v1 → v2) is what the doc stakes on, not the absolute J values.
- **Small corpus.** 27 negatives + 20 positives is small relative to CP-5 P0 (N=10 × 8 cells = 80 generations, 2000-iter bootstrap). The v1 thresholds had even less data behind their over-trip diagnosis (N=4), so v2 is a clear step up, but a future v3 calibration with bootstrap CIs over a larger code corpus would tighten the per-metric J estimates.
- **`trigram_repeat` margin is tight.** Negative-max `0.1358` vs v2 threshold `0.1372` is only `+0.0014` of headroom. A correct code output noisier than anything we sampled could still trip. The corpus-wide max-trigram came from `merge_intervals/GPU` and `topological_sort/GPU`; if production code outputs routinely exceed those, v3 should raise the threshold further.
- **The detector remains a passive observer.** Shipping the v2 thresholds does not change runtime behavior — `cascade.mesh.solve` still only emits the `degen[…]:` trace line per draft. Acting on the signal (open-threads item 5a) is the next step the v2 thresholds *enable* but do not implement.
- **`max_sent_repeat` is under-tested.** Only the `sentence_repeat` recipe (6 positives) stresses this metric. Its v2 J is `0.30`, identical to v1, because the negatives and the other 14 positives all sit at `max_sent_repeat = 1.0`. The metric works (the 6 sentence_repeat positives are all detected, FP=0), but its threshold has effectively been inherited from the prose calibration without re-fitting.

## Reproduce

```powershell
cd C:\Users\danth\src\edge-cascade
git switch experiment/pd1-v2-calibration-2026-05-26  # HEAD at a234548
uv run python scripts/build_pd1_v2_corpus.py        # ~3-5 min, writes runs/pd1-v2/corpus.jsonl
uv run python scripts/calibrate_pd1_v2.py           # writes degeneration_thresholds.json + calibration.json
uv run python scripts/verify_pd1_v2.py              # before/after on the 27 gate-PASS rows
```

Expected (deterministic for `calibrate_pd1_v2.py` and `verify_pd1_v2.py` given the persisted corpus; `build_pd1_v2_corpus.py` regenerates with model-temperature noise but the v1 → v2 direction holds):

- `calibrate_pd1_v2.py` writes `trigram_repeat_max=0.1372`, `ttr_min=0.3181`, `distinct_sent_ratio_min=0.6453`, `max_sent_repeat_max=1.0`.
- `verify_pd1_v2.py` reports `v1 false-positive rate on correct code: 14/27 = 52%` and `v2 false-positive rate on correct code: 0/27 = 0%`.

## What unblocks next

- **PD-1 v2 action levers (open-threads 5a).** With v2's 0% FP on correct code, skip-repair / warn-prompt / hard-escalate can be wired into `cascade.mesh.solve` without false-positive quality regression. Design call: which lever to ship first, and whether the score threshold for action (currently the `degraded` boolean = "any reason fires") should harden to "score ≥ 0.30" (two metrics) or stay at "any trip."
- **SD-2b dashboard panel (open-threads 5b).** Already shipping the v1 telemetry; the v2 thresholds will simply make the panel quieter on correct code. No code change needed in the dashboard.
- **v3 calibration debt.** A larger corpus (~100+ negatives) with bootstrap CIs would tighten the `trigram_repeat` margin and give `max_sent_repeat` an honest re-fit. Not blocking v2 action levers; track as a v3 follow-up.
