# DESIGN — PD-2 capture-risk rule: forbid strong→weak context hand-offs in the cascade

**Date:** 2026-05-28 · **Decision type:** architectural guideline (no code change in this doc) · **Status:** ACTIVE — applies to future cascade extensions

## TL;DR — the rule, then why

**Rule.** A change to the cascade MUST NOT route the output of a higher-capacity tier into the input context of a lower-capacity tier without a capture mitigation. "Capture" is the small-model failure mode quantified in the persona-debate study (`docs/FINDINGS-persona-debate-OVERVIEW.md`): a 1.5B model fed a 14B model's turns adopted the 14B's position outright; a critique/repair pass ordered to reverse the captured claims produced the *literal negation* of the instruction instead.

**Why this matters now.** The current cascade is structurally upward-only — NPU → iGPU/GPU → Tier 3 → cloud, weak-to-strong via escalation, with `prior_attempt` carrying weak outputs as context for strong consumers. That direction is safe. But several IDEAs on the backlog (and likely future ones) could *invert* the direction — e.g. "let Tier 3 critique a Tier 2 candidate and feed the critique back into a Tier 2 repair" or "let a 14B output condition a 1.5B summariser". Those are the cases this rule constrains.

## The evidence (quantified)

From `docs/FINDINGS-persona-debate-OVERVIEW.md` Secondary signal #1 — *Argumentative capture is a small-model effect*:

| Scenario | Model | `capture` score | Outcome |
|---|---|---:|---|
| Round 1 PRO Kant fed Trump's turns | qwen2.5-coder-1.5B (NPU) | up to **1.00** | PRO position abandoned; arguments parroted Trump |
| Round 2 PRO Singer fed Trump's turns | qwen2.5-coder-1.5B (NPU) | **0.56** | "best borders / fix our own country first" surfaced in Singer's turns |
| Round 3 PRO Kant fed Trump's turns | qwen2.5-coder:14b (GPU) | **≤ 0.30** | Kant rebutted the frame; capture did not occur |

Same persona, same opponent, same opponent-content; the only variable was model capacity at the weak side. Capture is a *capacity-bound* phenomenon, not a persona-specific one.

From Secondary signal #2 — *Corrective feedback can entrench capture*: a repair turn ordered to *reverse* the captured claims produced the literal negation of the instruction. Repairs restored content metrics (own-marker density rose across drafts) but did not undo the position drift. The implication, restated from that document verbatim:

> Piping a strong model's output into a weak one as context can capture it, and a critique/repair loop may not rescue it.

The headline `r = −0.92` (capacity vs looping) and `r = +0.89` (capacity vs lexical diversity) from the log analysis (`docs/FINDINGS-debate-log-analysis.md`) anchor the capacity-as-axis claim.

## The rule in operational form

> **PD-2 capture-risk rule.** Do not wire a higher-capacity tier's output into the context window of a lower-capacity tier. If a future feature genuinely needs that direction — e.g. a Tier-3 critique consumed by a Tier-2 retry — the design MUST include (a) an empirical capture-rate measurement on the proposed configuration, (b) an explicit mitigation (e.g. attribution stripping, paraphrase-from-scratch, model swap on critique), and (c) the mitigation's effect on the capture rate.

"Capacity" here = the same notion the project's other findings use (`metric-priorities-quality-cost-over-latency`, `llm-vram-capability`): the model's parameter count and effective reasoning bandwidth. The current cascade has three operationally-distinct capacity bands:

| Tier | Model band | Role |
|---|---|---|
| Tier 1 (NPU) | 1.5B sym-INT4 | Drafts |
| Tier 1b (iGPU) | 1.5B-ish | Sibling drafter (capacity-equivalent to Tier 1) |
| Tier 2 (GPU) | 14B Q4 | Generate / repair |
| Tier 3 (agent / cloud) | 32B-class subscription or Opus | Backstop / orchestration |

The capacity gradient is monotonic upward; the rule forbids a future architecture that lets Tier 2 or Tier 3 outputs flow *down* into Tier 1's input window, or Tier 3 outputs into Tier 2's input window, without the three-clause mitigation above.

## What this rule does NOT forbid

The rule is narrow on purpose — overshooting would block legitimate cascade work. Specifically OK:

- **Upward hand-off via `prior_attempt`.** The cloud_worker's `_compose_user(query, prior_attempt)` (`cascade/cloud_worker.py:99`) wraps a lower-tier's failed answer as context for a higher-tier retry. This is the *opposite* direction (weak → strong) and is the cascade's load-bearing escalation pattern.
- **Same-tier repair.** The GPU repair loop (`cascade/mesh.py:209-235`) feeds GPU output into GPU's own next round. No capacity asymmetry, no capture.
- **Failure-channel feedback.** The `build_repair_prompt` failure list (`cascade/feedback.py:47`) carries gate failures (assertion text + observed values) — pure data, no persona content. Not a "model output" in the capture sense; the FAILED CHECKS block is the assertion machine's voice, not another model's.
- **Telemetry / dashboard rendering.** Reading tier outputs into a renderer is not "context input" — there's no language-model consumer on the receiving end.
- **Stripped citations / quotations.** A higher-tier output quoted as a *clearly attributed and brief* reference (e.g. "Reviewer says line 42 fails X") for a lower-tier turn is borderline. The rule applies when the higher-tier output is the load-bearing context, not when it's a delimited reference.

## How to apply when reviewing future changes

When reviewing a PR that touches the cascade's context-passing edges (`cascade/mesh.py`, `cascade/wiring.py`, `cascade/cloud_worker.py`, `cascade/orchestrator.py`, or any new tier op signature), ask:

1. Does the change introduce a *new* path where one tier's output becomes another tier's input context?
2. If yes, are the source tier and the destination tier in the same capacity band, or is the destination higher-capacity? If yes to either, the rule does not apply — proceed normally.
3. If the destination is *lower-capacity*, the rule applies. Block the PR until the three-clause mitigation is satisfied (measurement, mitigation, post-mitigation rate).

For the agent (this CLI), this rule is invoked by `pr_review.py` reviews via the same memory channel that surfaces other architectural rules; the doc lives at a stable path so the reviewer can cite it.

## Concrete backlog implications

- **Tier-3 repair-from-candidate (`docs/FINDINGS-tier3-repair-from-candidate-phase0.md`, #77).** Direction: weak (capped GPU) → strong (Tier 3). Upward. The rule does NOT apply. If a future revisit ever inverts the direction ("have GPU consume a Tier-3 critique"), the rule kicks in.
- **PD-1 v2 hard-escalate (`docs/DESIGN-pd1-v2-hard-escalate.md`, #78).** Direction: weak (NPU draft, degen-tripped) → strong (Tier 3). Upward. Rule does NOT apply.
- **`/edge-cascade` skill's escalation step.** Failed draft → next-tier-up retry via `prior_attempt`. Upward. Rule does NOT apply.
- **Hypothetical "Tier 3 critic feeds Tier 2 retry"** (a natural-looking next IDEA when the cascade gets richer). Direction: strong → weak. Rule APPLIES. Would need empirical capture-rate measurement on the proposed configuration before shipping, plus a mitigation if rate is non-trivial. The persona-debate's `capture=0.56` at 1.5B is the kind of number that would block a default-on rollout; `capture≤0.30` at the destination model's capacity is the kind that lets the design through with a documented caveat.
- **Hypothetical "let a 1.5B summarise the 14B's output for dashboard display"** — borderline depending on whether the 1.5B's summary is *consumed* by another LM (rule applies if so) or *rendered* as text to the user (rule does not apply).

## Caveats / limits

- **n=1 per configuration in the source study.** The persona-debate experiments are qualitative (three rounds, one configuration each); `persona fidelity` was judged by the orchestrating agent. The capacity-vs-capture story is robust to that n because the metric (`capture` score) is computed offline from log-derived content overlap, not from the judge — but the *threshold* between "safe" and "unsafe" capacity ratios is not precisely calibrated. Future architectural changes that lean on a sharper threshold should run their own measurement.
- **The rule is band-coarse, not model-specific.** A "Tier 1 → Tier 1b" hand-off between two 1.5B-class models is within-band and therefore safe by this rule, even though both are weak. The rule's failure mode it protects against is capacity ASYMMETRY, not low capacity overall.
- **Capture in non-persona tasks is unmeasured.** The study was on persona-debate. Code generation, summarisation, and other task families might exhibit capture differently. The rule treats the persona-debate evidence as a worst-case proxy — coding tasks are likely less prone to position-drift, but the rule errs on the side of forbidding the pattern absent measurement.

## Re-evaluate when

- A future study quantifies capture rate on non-persona tasks; if rates are uniformly low across task types, this rule could be loosened to "measure if persona/role/argument task, default-allow otherwise".
- The cascade's tier capacity ratios change (e.g. Tier 2 upgrades to 32B, Tier 3 downgrades to a 14B local stand-in); the rule's threshold language ("higher capacity") stays valid but the OPERATIONALLY safe destinations shift, and this doc should be updated to point at the new band table.
- A genuine runtime guard is added (e.g. an automatic capture detector that flags strong→weak edges at solve-time); at that point the doc rule becomes the *spec* the guard implements, and the "block PR until mitigation" clause is enforced by code rather than by reviewer attention.

## Related work

- `docs/FINDINGS-persona-debate-OVERVIEW.md` — the source evidence; the capture scores quoted above.
- `docs/FINDINGS-debate-log-analysis.md` — the quantitative log dive (k-means capability axis, `r = −0.92` capacity-vs-looping).
- `docs/FINDINGS-pd1-v2-skip-repair.md` (#75) — the shipped lever that re-confirmed cascade's upward-only direction is sound on code tasks.
- `docs/DESIGN-pd1-v2-hard-escalate.md` (#78) — sibling decision doc on the third PD-1 v2 lever; sibling format / structure.
- [[metric-priorities-quality-cost-over-latency]] — the project's "rank by quality and \$cost, not throughput" directive that this rule operationalises for cascade design.
- [[llm-vram-capability]] — the capability-vs-throughput finding that confirms capacity (not speed) is the relevant axis.
