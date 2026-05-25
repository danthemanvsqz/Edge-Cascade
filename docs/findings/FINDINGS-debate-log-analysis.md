# FINDINGS — Persona-debate log deep-dive: what clusters together

**Type:** quantitative analysis of the three persona-debate rounds' telemetry
(`FINDINGS-debate-kant-trump.md`, `-singer-trump.md`, `-kant-trump-gpu-duel.md`).
**Date:** 2026-05-25. **Tool:** `scripts/debate_analysis.py` (stdlib + numpy,
seeded → reproducible; reads `runs/experiment-debate/*/state.json`, $0).
**Data:** 22 final turns + 6 repair drafts across 4 runs (rounds 1–3 + a smoke).

## TL;DR / decision

Feature-per-turn clustering (k-means, k=3, on content features only — no model
label given) **recovers a clean capability axis**, and shows the small-model
failure is **not one blob but two distinct pathologies**. Capacity correlates
strongly with *fluency/non-degeneration* (looping r=−0.92, lexical diversity
r=+0.89) but **barely with persona-vocabulary density (r=+0.14)** — the 1.5B
*knows the persona's words; it just can't stop repeating them or hold first
person.* This nails down, numerically, that the rounds-1/2 collapses were a
capacity wall, and tells the cascade *which* failure to detect (looping/diversity,
cheap to measure) rather than vocabulary.

## The three clusters

K-means split the 22 turns almost perfectly by capability:

| Cluster | n | Members | distinct-sent | trigram-repeat | TTR | own/100w | Character |
|---|---|---|---|---|---|---|---|
| 0 coherent | 14 | all 14B (10 r1-Trump, 3 qwen-Kant) + 1 lucky 1.5B opening | 0.99 | 0.02 | 0.61 | 4.1 | fluent, on-persona, no looping |
| 1 vacuous/captured | 3 | 1.5B only | 0.93 | 0.29 | 0.37 | 0.5 | content collapse; opponent markers ≈ own |
| 2 looping zealot | 5 | 1.5B only | 0.83 | 0.39 | 0.30 | 5.2 | loops hard while parroting its OWN doctrine words |

The bifurcation is the new signal: small-model failures separate into **content
collapse** (cluster 1: lost its own jargon, drifts toward the opponent) vs
**jargon looping** (cluster 2: high own-marker density *and* high repetition).

## Capacity vs pathology (Pearson r over final turns)

| Feature vs model size (B params) | r | Reading |
|---|---|---|
| trigram-repeat | **−0.92** | bigger → almost no looping (strongest) |
| lexical diversity (TTR) | **+0.89** | bigger → far more varied wording |
| distinct-sentence ratio | +0.66 | bigger → fewer repeated sentences |
| max single-sentence repeat | −0.59 | bigger → no degenerate restating |
| third-person self-reference | −0.50 | bigger → stays in first person |
| word count | −0.63 | the 1.5B pads with repetition (longer turns) |
| **own-marker density** | **+0.14** | ~none — the small model knows the words |

## Qualitative findings, quantified

- **Argumentative capture** (1.5B-only): round-2 Singer rebuttal `capture=0.56`
  (more *Trump* markers than Singer's own); round-1 Kant closing `capture=1.00`
  (zero own markers). 14B Kant tops out at 0.30 and always own > opponent.
- **Third-person persona breaks**: `self_3p` 8 / 8 / 6 on 1.5B turns; **0** on
  every 14B turn.
- **Utilitarian-in-Kant conflation**: heaviest in 1.5B Kant (2.9, 1.8 /100w);
  0.4 for 14B Kant; 0.6 for Singer is *correct* (he is a utilitarian).
- **Repairs restore content but don't fix looping**: round-2 Singer idx2 own-marker
  density rose 0 → 0.9 → 3.4 across drafts (content returned), yet distinct-sentence
  ratio stayed ~0.72; one repair even *worsened* looping (0.55). Repair targets the
  symptom you name, not the degeneration.
- **deepseek-r1 `<think>`: 0/10** turns emitted a trace under a persona prompt
  (all three rounds) — reproducible suppression.

## Method / reproduce

```
.venv/Scripts/python scripts/debate_analysis.py
```
Features per turn: loop metrics (distinct-sentence ratio, max single-sentence
repeat, trigram-repeat), lexical diversity (type-token ratio), persona-marker
densities for the speaker's OWN vs the OPPONENT's persona (lexicon substring
counts), a capture ratio = opp/(own+opp), utilitarian-marker density, third-person
self-reference (own-surname mentions minus "I am <name>" intros), and
length/latency/throughput. K-means is a seeded numpy Lloyd's implementation
(no sklearn). The loader normalises three harness-version schemas.

## Caveats

n is small (22 turns); k-means on n=22 with 7 features is exploratory — clusters
are stable here because the capability gap is large. Marker lexicons are
hand-built substring lists (a philosopher *quoting* the opponent to rebut them
inflates opp-markers without true capture — so capture is a proxy, cross-checked
against the transcripts). "Size" is a 1.5B-vs-14B proxy and conflates parameter
count with the NPU/GPU substrate. Single run per configuration.
