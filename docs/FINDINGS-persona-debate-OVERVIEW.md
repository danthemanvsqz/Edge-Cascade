# FINDINGS — Cross-tier persona debates (overview of three experiments)

A qualitative `experiment`-skill program (persona debates on the local mesh, $0).
A philosopher debates Donald J. Trump on one motion; we swapped the philosopher,
the models, and the substrate across three rounds and analysed the logs. **Start
here**, then read the per-round docs. Raw transcripts/telemetry stay on the
evidence branches cited below (not in this PR).

## The motion (Peter Singer's argument)

> *"Because global connectivity erases the excuse of distance, failing to give
> until it hurts to save lives abroad is a total moral failure."*

Drawn from Singer, *Famine, Affluence, and Morality* (1972) — proximity/distance
is morally irrelevant to the duty to prevent suffering.

## The three rounds (all judged on persona fidelity)

| # | PRO (handicapped?) | CON | Substrate | Winner | Detail doc |
|---|---|---|---|---|---|
| 1 | Kant — 1.5B (NPU), handicapped | Trump — deepseek-r1:14b | NPU vs GPU | **Trump**, decisive | `FINDINGS-debate-kant-trump.md` |
| 2 | Singer — 1.5B (NPU), handicapped | Trump — deepseek-r1:14b | NPU vs GPU | **Trump**, narrow | `FINDINGS-debate-singer-trump.md` |
| 3 | Kant — qwen2.5-coder:14b | Trump — deepseek-r1:14b | GPU vs GPU, symmetric | **Kant**, narrow | `FINDINGS-debate-kant-trump-gpu-duel.md` |

Round-1 Kant got handicaps (speaks first+last, larger token budget, agent-coached
repairs) to compensate for the 1.5B; they did not close the gap. Round 3 removed
handicaps and put a 14B behind the philosopher — and the philosopher finally won.

## Headline finding

**Model capacity — not the persona, the side, or the coder-model family — gates
persona fidelity.** The same Kant prompt that conflated doctrine, looped, and got
captured at 1.5B argued coherently and correctly at 14B (qwen-coder). The log
deep-dive (`FINDINGS-debate-log-analysis.md`) makes this quantitative:

- K-means on per-turn content features (no model label given) recovers a clean
  **capability axis**, and the small-model failure **bifurcates** into two clusters:
  *content collapse* (loses its own jargon, drifts toward the opponent) vs *jargon
  looping* (high own-marker density **and** high repetition).
- Capacity vs **looping r = −0.92** and vs **lexical diversity r = +0.89**, but vs
  **persona-vocabulary density only r = +0.14** — *the small model knows the words;
  it just can't stop repeating them or hold first person.* So the cheap thing to
  detect is degeneration (looping/diversity), not vocabulary.

## Secondary signals (all reproduced / quantified)

1. **Argumentative capture is a small-model effect.** Fed a stronger opponent's
   turns as context, the 1.5B adopted the opponent's position (round-2 Singer
   parroted Trump's "best borders"/"fix our own country first"; `capture=0.56`,
   `1.00`). It **vanished at 14B** (Kant rebutted the frame instead, `capture≤0.30`).
2. **Corrective feedback can entrench capture.** A repair explicitly ordered to
   *reverse* the captured claims produced the literal negation of the instruction.
   Repairs restored *content* (own-marker density rose across drafts) but did **not**
   fix looping. *Implication for the cascade: piping a strong model's output into a
   weak one as context can capture it, and a critique/repair loop may not rescue it.*
3. **Doctrine/voice errors are 1.5B-only:** utilitarian-in-Kant conflation (the
   1.5B asserted the categorical imperative "states… the principle of utility");
   third-person persona breaks ("Peter Singer is a philosopher who…", `self_3p`
   8/8/6 vs **0** at 14B).
4. **deepseek-r1 emits no `<think>` under a persona prompt** — 0/10 Trump turns
   across all three rounds. A persona/system prompt reliably suppresses r1's
   reasoning trace (worth a controlled check vs a neutral prompt).
5. **Trump (a high-surface rally voice) is an easy, robust persona** for a 14B; the
   only fidelity slip was r1's round-3 *closing* drifting into measured analysis.

## Decision / implications

- For persona/role tasks needing doctrinal coherence, **~14B is the usable floor**
  here; the 1.5B NPU tier is not — consistent with `metric-priorities` (quality over
  throughput) and the `llm-vram-capability` capability findings.
- A cheap **degeneration guard** (sentence-repeat / lexical-diversity thresholds)
  would catch the dominant small-model failure mode; vocabulary checks would not.
- Treat strong→weak context hand-offs in the cascade as a **capture risk**; don't
  assume a repair/critique pass can undo it.

## Harness / reproduce ($0, local)

- `scripts/debate.py` — configurable debate driver: slots `a`/`b`, each with a
  backend (`openvino` NPU or `ollama` GPU model), per-slot model, optional
  `--handicaps`, `--citation`; streams a live transcript; segregated telemetry via
  `make_experiment_recorder`. Example (round 3):
  ```
  .venv/Scripts/python scripts/debate.py run --rounds 3 \
    --a-persona kant --a-model qwen2.5-coder:14b --a-side pro \
    --b-persona trump --b-model deepseek-r1:14b --handicaps none --citation fam1972
  ```
- `scripts/debate_analysis.py` — the log deep-dive (seeded numpy k-means, $0).
- Also includes two general fixes surfaced by this work: `*.rec binary` in
  `.gitattributes` and `exclude: ^runs/` on the whitespace/eof pre-commit hooks, so
  the byte-length-framed recorder evidence is never mutated on commit.

## Evidence (raw transcripts + telemetry — NOT merged; do not delete)

Per the experiment protocol the evidence lives on labelled local branches:

| Round | Branch | sha | Run id |
|---|---|---|---|
| 1 | `experiment/kant-trump-debate-2026-05-25` | `7970916` | `20260525T200202Z` |
| 2 | `experiment/singer-trump-debate-2026-05-25` | `f33de75` | `20260525T201913Z` |
| 3 + log analysis | `experiment/kant-trump-gpu-duel-2026-05-25` | `3e337ec` | `20260525T203552Z` |

Each branch carries `runs/experiment-debate/<run-id>/{live.md,state.json}` and the
segregated `runs/experiment-debate.rec` (`git add -f`'d). n = 1 per configuration;
"persona fidelity" was judged by the orchestrating agent — treat as hypotheses.
