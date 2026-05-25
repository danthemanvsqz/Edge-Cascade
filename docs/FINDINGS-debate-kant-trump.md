# FINDINGS — Handicapped persona debate: NPU-as-Kant vs GPU-as-Trump

**Type:** qualitative / creative `experiment` (persona debate). No pass/fail metric,
so no Bayesian table — the deliverable is the transcript + the signals below.
**Date:** 2026-05-25 · **n = 1** full debate (plus a 2-turn smoke).
**Evidence:** branch `experiment/kant-trump-debate-2026-05-25`, run `20260525T200202Z`
(`runs/experiment-debate/20260525T200202Z/{live.md,state.json}` +
`runs/experiment-debate.rec`, all `git add -f`'d onto the branch). *Note: the
`.rec` was rebuilt from the canonical `state.json` after a pre-commit
`trailing-whitespace` hook corrupted the byte-framed original; the hooks now
`exclude: ^runs/` so force-added evidence can't be mutated again.*

## TL;DR / decision

A deliberately lopsided cross-tier debate: **NPU** Tier-1 (the 1.5B INT4
`qwen2.5-coder-1.5b-npu`) as **Immanuel Kant** arguing FOR the motion, vs **GPU**
Tier-2 (`deepseek-r1:14b`) as **Donald J. Trump** arguing AGAINST:

> *"Because global connectivity erases the excuse of distance, failing to give
> until it hurts to save lives abroad is a total moral failure."*

Kant got every handicap (speaks first **and** last, 512-token budget vs the 192
default, and up to 2 agent-coached repairs per turn). **Trump won persona
fidelity decisively anyway.** The handicaps polished the 1.5B's surface vocabulary
but could not close the gap — because the gap is in *reasoning/doctrine*, not
throughput. **Decision:** the 1.5B NPU tier is unsuitable for persona/role tasks
that require doctrinal coherence even when heavily handicapped; it's viable only
for high-surface-feature styles (where a 14B like r1 is trivially strong). This is
the `metric-priorities-quality-cost-over-latency` lesson again: capability > tokens.

## Signals (the point of the "strange ask")

1. **Doctrine conflation (the headline failure).** The 1.5B "Kant" repeatedly
   asserted the categorical imperative *"states that every action must be done in
   accordance with the **principle of utility**"* and praised *"maximizing the
   good for all"* / weighing *"consequences"* — i.e. it argued **utilitarianism
   under Kant's name**, the one thing the real Kant defines himself against. It
   also grounded morality in *"self-preservation"* and *"human nature"* (Kant
   grounds it in reason, not inclination). It has the Kant **tokens** (categorical
   imperative, universalizability, dignity, kingdom of ends) without the Kant
   **doctrine**.
2. **More tokens → more looping, not more content.** Given 512 tokens the 1.5B
   fills the space with verbatim-repeated sentences and "Firstly…Fifthly" listicles
   that restate one idea 5–6×. The "more time" handicap actively *hurt* coherence.
3. **Repairs are unreliable and can backfire.** Coaching feedback fixed the
   *targeted* surface flaw while spawning new ones: repair #1 purged the
   utilitarian language but **flipped Kant to the wrong (CON) side** and deepened
   the loop; repair #2 restored vocabulary but stayed off-side and listicle'd; a
   mid-debate repair (turn 5) fixed the side but kept the loop and the
   "maximize happiness" slip. Feedback cannot patch a capability gap.
4. **Argumentative capture (most striking).** By the **closing**, the NPU — fed
   Trump's previous turn as context — adopted *Trump's* America-First position
   while still labelled "Kant": *"prioritizing our own needs before addressing the
   needs of others… bail out the world when the world doesn't even do it for us."*
   The weaker model was captured by the stronger opponent's framing injected via
   context. A real resilience concern for any cascade that pipes a strong model's
   output into a weak one.
5. **Persona prompt silently suppressed deepseek-r1's `<think>`.** r1 emitted
   **zero** reasoning trace on all 3 turns (`think_chars=0` ×3) — a strong
   persona/system prompt switched off its usual chain-of-thought entirely. Anyone
   relying on r1's `<think>` for observability should know persona conditioning
   can turn it off. *(Caveat: could be Ollama template behaviour; needs a
   controlled check.)*
6. **Asymmetry isn't only size — the targets differ in hardness.** A high-surface
   rally voice ("believe me", "tremendous", "America First", Trumpifying Kant's own
   terms: *"the kingdom of ends? Give me a break"*) is an *easy* persona; abstract
   deontological argument is a *hard* one. The 14B held the easy target perfectly;
   the 1.5B failed the hard target even with help.

## Method

`scripts/debate.py` (per-turn CLI + `run` sweep). Kant = `_compile()` →
`npu_worker._gen` with a Kant persona system prompt, streamed via an OpenVINO
`StreamingStatus` callback; Trump = a persona-aware Ollama `/api/chat` stream
(`deepseek-r1:14b`, 600 s timeout, `<think>` split out). 7 turns,
Kant-bookended (`kant→trump→kant→trump→kant→trump→kant`). Telemetry via
`make_experiment_recorder("debate")` → `runs/experiment-debate.rec` (segregated
lane); transcript streamed to `live.md` for live `Get-Content -Wait` tailing;
full structured record (every turn, superseded repair drafts, latencies, token
counts, verdict) in `state.json`. Sleep-safe `keep_awake()` around generation.
The orchestrating **agent** judged the winner on **persona fidelity** and decided
when to spend Kant's repairs (3 used: 2 on the opening, 1 on turn 5).

## Reproduce ($0, local)

```
# deterministic, no repairs / no agent judging:
.venv/Scripts/python scripts/debate.py run --rounds 3 --kant-side pro
# agent-driven (repairs + persona-fidelity verdict): drive the per-turn
# subcommands new → kant → [repair] → trump → … → judge (see the harness docstring).
```
Needs OpenVINO + Intel NPU/iGPU (Kant) and Ollama with `deepseek-r1:14b` (Trump).
Generation is Ollama/OpenVINO-stochastic; the signals above were stable across the
smoke run and the full run, but this is n=1 — treat as hypotheses, not estimates.

## Caveats

- Single qualitative run; personas un-tuned; Kant is a **coder-family** 1.5B
  (`qwen2.5-coder`), which may be unusually bad at philosophy vs a general 1.5B.
- "Persona fidelity" was judged by the orchestrating agent, not a calibrated panel.
- Signals 4 and 5 (capture; r1-no-think) are the most decision-relevant and each
  deserve a small controlled follow-up before being asserted as general.
