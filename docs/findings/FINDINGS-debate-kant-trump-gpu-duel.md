# FINDINGS — Persona debate round 3: GPU-vs-GPU duel (Kant vs Trump, 14B vs 14B)

**Type:** qualitative `experiment` (persona debate), round 3. Follow-up to
`FINDINGS-debate-kant-trump.md` (round 1, NPU-Kant vs GPU-Trump) and
`FINDINGS-debate-singer-trump.md` (round 2, NPU-Singer vs GPU-Trump).
**Date:** 2026-05-25 · **n = 1** full debate.
**Evidence:** branch `experiment/kant-trump-gpu-duel-2026-05-25`, run
`20260525T203552Z` (`runs/experiment-debate/20260525T203552Z/{live.md,state.json}`
+ `runs/experiment-debate.rec`, `git add -f`'d).

## Config

GPU-only, **symmetric (no handicaps, no repairs, equal turns)**, both 14B:
- **a = Immanuel Kant** on `qwen2.5-coder:14b` (PRO)
- **b = Donald J. Trump** on `deepseek-r1:14b` (CON)
- Motion as before, now carrying a scholarly citation in the prompt:
  *Peter Singer, "Famine, Affluence, and Morality" (1972)* — proximity/distance is
  morally irrelevant. Plan `a→b→a→b→a→b` (6 turns), Kant opens.

## TL;DR / decision

**The first round the philosopher wins** — Kant (qwen2.5-coder:14b) takes persona
fidelity over Trump (deepseek-r1:14b), narrowly. This **resolves the central
question from rounds 1–2**: the philosopher's collapses there (doctrine conflation,
repetition loops, listicles, third-person drift, argumentative capture) were the
**1.5B model's capacity**, *not* the persona, the side, or the coder family. Put a
**coder 14B** behind the same Kant prompt and it argues coherently, correctly, and
in-register for the whole debate. **Decision:** for persona/role tasks needing
doctrinal coherence, model capacity is the gate; ~14B is the usable floor here and
the 1.5B NPU tier is not (consistent with `metric-priorities-quality-cost-over-latency`
and the `llm-vram-cliff-12gb` capability findings).

## Signals

1. **14B coder holds the persona; 1.5B did not (headline).** qwen2.5-coder:14b as
   Kant: correct categorical imperative ("maxims that can be universalized without
   contradiction"), humanity "as an end in itself", the "kingdom of ends", firmly
   PRO, directly responsive ("Mr. Trump's assertion that we should focus on America
   first…"), and **none** of the 1.5B pathologies — no loops, no listicles, no
   third-person drift, no capture. Same prompt that broke at 1.5B works at 14B.
2. **A capable model uses the supplied citation.** Given the Singer (1972) reference
   in the prompt, Kant grounded the argument in it *every turn* ("Peter Singer's
   drowning-child scenario illustrates…"). The 1.5B never used a reference like this.
   Useful for the cascade: in-context scholarly grounding only "takes" on a capable
   model.
3. **No argumentative capture at 14B.** The round-2 failure mode (the weak model
   adopting the opponent's framing — "best borders in the world") did **not** recur;
   Kant rebutted Trump's frame instead of absorbing it. Capture looks like a
   small-model context-susceptibility effect, not a general one.
4. **The stronger/more-reasoned model drifted in its CLOSING.** Trump (r1) was vivid
   and on-voice for most of the debate ("believe me", "Mexico isn't paying for the
   wall", "nobody knows borders better than me") but his closing broke character
   toward structured analysis — a literal "**Closing Statement:**" heading and
   measured lines ("respects the complexities of international relations", "striking
   a balance") Trump would not voice. The decisive fidelity gap. (Plausibly the
   "closing" framing pulling a reasoning-tuned model toward summary/synthesis.)
5. **r1 emitted zero `<think>` — third round running.** `think_chars=0` on all three
   Trump turns again. A persona system prompt reliably suppresses deepseek-r1's
   reasoning trace across all three rounds (worth a controlled check vs a neutral
   prompt, but the effect is now very consistent).
6. **Operational:** two 14B models alternating force an Ollama model **swap each
   turn** (12GB VRAM can't co-reside ~9GB+~9GB) — it worked fine, ~12–30 s/turn.

## Method / harness notes

`scripts/debate.py` generalised this round: slots `a`/`b`, each with a `backend`
(`openvino` NPU or `ollama` GPU model) and per-slot model; `--handicaps {none,a,both}`;
`--citation fam1972`. Agent ran the 6 turns and judged persona fidelity. A
console-encoding bug was found and fixed mid-run: the streamed stdout echo used the
Windows cp1252 codepage and crashed a turn on a non-cp1252 char r1 emitted; fixed by
forcing UTF-8 (`sys.stdout.reconfigure(errors="replace")`) — the `live.md` file write
was always UTF-8 and unaffected. The partial turn was truncated from `live.md` and
re-run; `state.json` (canonical) only ever held complete turns.

```
.venv/Scripts/python scripts/debate.py run --rounds 3 \
    --a-persona kant --a-model qwen2.5-coder:14b --a-side pro \
    --b-persona trump --b-model deepseek-r1:14b --handicaps none --citation fam1972
```

## Caveats

n = 1, stochastic; "persona fidelity" judged by the orchestrating agent (and round 3
was close — a reasonable judge could score it the other way). Signals 4–5 (closing
drift; r1 `<think>` suppression) are the most decision-relevant follow-ups.
