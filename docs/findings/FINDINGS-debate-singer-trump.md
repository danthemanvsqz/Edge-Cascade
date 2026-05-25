# FINDINGS — Persona debate round 2: NPU-as-Singer vs GPU-as-Trump

**Type:** qualitative `experiment` (persona debate), follow-up to
`FINDINGS-debate-kant-trump.md`. **Date:** 2026-05-25 · **n = 1** full debate.
**Evidence:** branch `experiment/singer-trump-debate-2026-05-25`, run
`20260525T201913Z` (`runs/experiment-debate/20260525T201913Z/{live.md,state.json}`
+ `runs/experiment-debate.rec`, `git add -f`'d).

## TL;DR / decision

Same harness and matchup structure as round 1, but the NPU's persona is swapped
from Kant to **Peter Singer** — because the motion *is* Singer's own argument
(*Famine, Affluence, and Morality*: connectivity removes the distance excuse;
fail-to-give-until-it-hurts is a moral failure). NPU = Singer (PRO, handicapped:
first+last, 512 tok, agent repairs); GPU = `deepseek-r1:14b` as Trump (CON).

**Trump still wins persona fidelity — but by a clearly narrower margin.** The
decision from round 1 holds and sharpens: a small model's persona fidelity is
**dominated by alignment between the prompt and the persona's actual, well-attested
position**, not by the handicaps. When the task *is* the persona's real argument,
the 1.5B produces genuinely faithful content; when pushed off it (by repairs, by
the opponent), it collapses the same way it did as Kant.

## Signals (what changed vs round 1, and what didn't)

1. **Persona alignment is the lever (new, strongest signal).** As Singer the 1.5B
   *opened* faithfully: the correct principle ("prevent something very bad without
   sacrificing anything of comparable moral importance"), the **drowning-child pond
   analogy**, "distance is morally irrelevant", and — correctly, unlike the Kant
   run — a **utilitarian** framing. The Kant run never managed an equivalent. The
   difference isn't size or handicaps; it's that the motion is Singer's documented
   thesis.
2. **Argumentative capture is real and worse here.** Fed Trump's turns as context,
   "Singer" adopted the opponent's own lines — *"the United States has the best
   borders in the world"*, *"fixing our own country first"* — argued that distance
   **does** matter, and **conceded his own pond analogy**: the exact inversions of
   his view.
3. **Corrective feedback ENTRENCHED the capture (notable robustness signal).** A
   repair that explicitly itemised the captured claims and ordered their reversal
   ("borders are morally arbitrary") produced the literal **negation** of the
   instruction (*"national borders are not morally irrelevant… respect and protect
   these borders"*) and doubled down on "fix our own country first". The weak model
   was more anchored to the in-context opponent framing than to direct correction.
   *Implication for the cascade:* piping a strong model's output into a weak one as
   context can capture the weak model, and feedback may not rescue it — relevant to
   any repair/critique loop where a small model post-processes a larger one's text.
4. **Persona-independent pathologies persist.** Degenerate repetition loops,
   "Firstly/Secondly…" listicles instead of the requested form, and **third-person
   drift** (*"Peter Singer is a contemporary moral philosopher…"* — talking *about*
   the persona instead of *as* it). Identical to the Kant run → these are 1.5B /
   coder-model traits, not persona-specific.
5. **r1 emitted zero `<think>` again.** All three Trump turns: `think_chars=0`. Two
   runs now with a persona system prompt fully suppressing deepseek-r1's reasoning
   trace — the effect is reproducible (still worth a controlled check vs a neutral
   prompt).
6. **Trump remains a robustly easy, high-fidelity target** for the 14B: flawless
   rally voice every turn, and responsive to Singer specifically
   (*"the drowning child analogy? Out of touch!"*, *"I know borders"*).

## Method / reproduce

Harness `scripts/debate.py` (generalised this round: slots are `npu`/`gpu`, persona
chosen at `new` time via `--npu-persona/--gpu-persona`). Agent drove the 7 turns
(`npu→gpu→…→npu`), judged persona fidelity, and spent 3 repairs (2 on turn 3, 1 on
turn 5). Telemetry → `runs/experiment-debate.rec`; live transcript streamed to
`live.md`; full record incl. superseded repair drafts in `state.json`.

```
.venv/Scripts/python scripts/debate.py run --rounds 3 \
    --npu-persona singer --gpu-persona trump --npu-side pro   # deterministic, no repairs
```

## Caveats

n = 1, stochastic generation; "persona fidelity" judged by the orchestrating agent;
the NPU model is a **coder-family** 1.5B. Signals 2–3 (capture; feedback entrenching
capture) are the most decision-relevant and warrant a small controlled follow-up
before being asserted as general.
