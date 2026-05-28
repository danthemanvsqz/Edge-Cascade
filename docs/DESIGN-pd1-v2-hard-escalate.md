# DESIGN — PD-1 v2 `hard-escalate` lever: SHELVE (evidence-grounded decision, no measurement run)

**Date:** 2026-05-28 · **Decision type:** Architecture Decision Record (ADR) · **Status:** SHELVED — not built, with explicit re-trigger conditions

## TL;DR — existing evidence already answers the question

PD-1 v2 enumerated three orthogonal action levers on the degeneration signal:
1. **warn-prompt** (thread degen reasons into the repair prompt) — REVERTED via #69/#70 (`-4.4 pp pooled`, `docs/FINDINGS-pd1-v2-warn-prompt.md`).
2. **skip-repair** (discard the poisoned NPU prior, let GPU do a fresh `generate`) — SHIPPED via #74/#75/#76 default-on (`+22.8 pp pooled`, `docs/FINDINGS-pd1-v2-skip-repair.md`).
3. **hard-escalate** (promote the degen-tripped draft straight to Tier 3, skipping the GPU phase entirely).

This doc decides #3. **Verdict: SHELVE.** Not built. The combined evidence from the closed levers makes the headroom analysis unfavourable AND surfaces a paid-cost regression in the production execution mode where the lever would actually be a code change. No new measurement was run for this decision — the evidence from #75 + #77 is load-bearing enough.

## Context — what hard-escalate would do

The cascade emits a `DegenerationResult` for every drafted candidate (NPU and iGPU tiers). `score` is a 0..1 aggregate of looping / narrowing / truncation metrics; `text_reasons` lists the metric(s) that tripped. The v2 calibration (`docs/FINDINGS-pd1-v2-calibration.md`) puts the production floor at 0.30 with 0 FP on 27 correct-code negatives.

Today (post-#76) when the score crosses the floor:

```
NPU draft -> degen trip -> skip-repair fires:
  discard prior, prior_degen=()
  GPU phase: fresh `generate(query)` (not a repair)
  -> if PASS: resolved at Tier 2
  -> if FAIL: GPU enters bounded repair loop on its own output
  -> if loop caps: handoff to Tier 3 (capped->tier3)
```

Hard-escalate would change the second line: on a degen trip, skip GPU entirely and hand off to Tier 3 immediately.

```
NPU draft -> degen trip -> hard-escalate fires:
  discard prior, prior_degen=()
  GPU phase: SKIPPED
  -> handoff to Tier 3 directly (escalated->tier3)
```

The trigger is identical to skip-repair (`degen.score >= CONFIG.skip_repair_score_floor`); the difference is *which downstream tier handles the degen case*.

## Execution modes — the asymmetry that drives the decision

The cascade has two execution surfaces, and "Tier 3" means different things in each:

### Mode A — programmatic orchestrator (`cascade/orchestrator.py::run_pipeline`)

- Mesh runs in-process; on cap (`mesh.Outcome.capped=True`), the orchestrator calls `cloud_worker.generate(query)` — the **paid Anthropic API**.
- Hard-escalate in Mode A = route degen-tripped queries to the paid cloud tier *before* trying GPU.
- The cost dimension is real money: `claude-opus-4-7` at $15 in / $75 out per 1M tokens (`cascade/cloud_worker.py:_PRICES`). Every additional escalation has a non-zero $ delta.

### Mode B — agent loop (`/edge-cascade` skill, this CLI session)

- The agent IS Tier 3; the cascade is a tool the agent calls.
- The `/edge-cascade` skill already routes `difficulty >= 0.80` directly to the agent (skipping the local tiers).
- Hard-escalate in Mode B = extend that direct-to-agent route to also trigger on "NPU draft degen-tripped", regardless of router difficulty.
- The cost dimension is subscription marginal: $0 per-call but consumes agent context budget; cumulative across a long session this matters but per-call cost is not a hard blocker.

These are different code paths and would need different changes. The Mode B trigger already exists at the routing layer in skill prose; adding a second trigger condition is a SKILL.md edit, not a cascade code change. The Mode A trigger requires changing `cascade.mesh.solve` (currently the degen check sits at lines 179-193 and discards-then-falls-through to GPU; hard-escalate would discard-and-return-capped-with-a-different-final-tier).

## Evidence — what we already measured

### Skip-repair (PR #75/#76, evidence `691b4e2`)
- **Pooled +22.8 pp** correctness vs the pre-lever cascade.
- **FIRED sub-pool +33.7 pp** on subjects above the floor (the cases where the lever actually engages).
- **NULL sub-pool -0.03 pp** below the floor — clean invariant.

The mechanism story: when the NPU draft trips degen, the GPU's *fresh* generate (not a repair on the poisoned prior) recovers most cases. That's the load-bearing observation: **GPU is competent on degen-tripped tasks when freed from the bad prior**.

### Tier-3 repair-from-candidate Phase 0 (PR #77, evidence `ca81a5e`)
- Synthetic 10-task battery: **1/10 caps**.
- Production `runs/cascade.rec`: **0/6 caps** in available sample.
- Conclusion: caps are RARE in the post-skip-repair cascade.

Re-reading this evidence through the hard-escalate lens: of the degen-trip cases, ~90% resolve at Tier 2 (the fresh-GPU path). The 1/10 (`html_attr_parse`) is the case where fresh-GPU + bounded repair *also* failed — the "hard-escalate could have helped here" slice.

## Headroom analysis — how much room is left for hard-escalate to help

Let `D` = P(degen-trip on a real task) ≈ small (the 0.30 floor is calibrated for 0 FP on correct code). Let `F` = P(fresh-GPU resolves | degen-trip) — directly measured by #75's FIRED-arm correctness:

| Sub-pool quantity                                  | Value (from #75) |
|----------------------------------------------------|-----------------:|
| FIRED ctrl (skip-repair off, repair-on-poisoned)   | 32/60 = 0.533    |
| FIRED trt (skip-repair on, fresh GPU)              | 53/60 = **0.870** |
| Marginal cap rate AFTER skip-repair                | ~13%             |

So of all degen-trips:
- ~87% — fresh GPU resolves at Tier 2 (skip-repair handled it).
- ~13% — fresh GPU also fails; cascade caps; today's flow escalates to Tier 3 via `mesh.solve -> capped -> orchestrator escalates`.

Hard-escalate would intercept ALL degen-trips (the 87% + 13%) and route them to Tier 3 instead of GPU.

The Mode-A cost arithmetic on the 87% slice (the cases where fresh-GPU would have solved it):
- **Today:** GPU `generate` (~$0 marginal local) → resolved at Tier 2.
- **Under hard-escalate:** Tier 3 `claude-opus-4-7` call → likely resolved at $0.05-0.20 per call (Opus on a ~1k-token repair-style task).

For each degen-trip query, hard-escalate in Mode A trades $0 local for ~$0.10 paid cloud. Even at low absolute volumes ($10/day cap on `review_daily_usd`), this would pressure the production credit guard for queries that would have resolved locally.

The 13% slice (the cases where today's flow already caps to Tier 3) is the only band where hard-escalate is cost-equivalent in Mode A. That's the slice tier3-repair-from-candidate Phase 0 was trying to measure. Hard-escalate doesn't help here either — it just changes the upstream path; the resulting Tier 3 call is the same.

## Risks if we built it

### Mode A
- **Paid cloud regression.** ~87% of degen-trips would shift from $0 GPU to ~$0.10 Opus. This is a directional cost increase with no clear quality justification: #75 already showed the skip-repair fresh-GPU path achieves 0.870 correctness on the FIRED sub-pool. Tier 3 would need to beat that by ≥10 pp (the same SHIP threshold from the v2 decision rule) to justify the cost, and there's no prior reason to expect that gap on this task band.
- **Credit-guard pressure.** The cascade currently runs at near-$0 daily local spend; this lever would route a stochastic fraction of queries to the paid lane, increasing daily-spend variance and triggering guard limits in edge cases.

### Mode B
- **Already exists at routing.** The `/edge-cascade` skill's step-3 protocol routes `difficulty >= 0.80` directly to the agent. Adding "NPU draft degen-tripped" as a second trigger is a single-line SKILL.md addition. The decision to NOT make that addition is the conservative default: the existing route (difficulty-based) already captures the genuinely-hard-for-locals queries, and degen-on-draft is a quality signal not a difficulty signal.
- **Context budget.** Every direct-to-agent escalation consumes session context; cumulative across long sessions this displaces other tool calls.

### Regression channel (both modes)
- Hard-escalate would BYPASS skip-repair entirely (skip-repair runs INSIDE the GPU phase; if GPU is skipped, skip-repair never fires). That means the +22.8 pp pooled correctness from #75 disappears for the degen-trip slice. We'd need to measure that Tier 3 recovers what skip-repair did, and at what cost.

## Decision: SHELVE

Hard-escalate as a code-level cascade lever is **not built**.

Reasoning:
1. The 87% slice where skip-repair already wins doesn't need a second escalation. Routing those queries to paid cloud instead trades correctness-neutral local work for paid work with no quality gain.
2. The 13% remaining slice (today's cap rate) is where any benefit would land, but #77 showed that band is too small for the existing-lever-against-baseline question. Hard-escalate would have the same scale problem.
3. Mode B already has the lever shape via the difficulty-route trigger in `/edge-cascade`; a second trigger would be incremental, not structural, and is not load-bearing.
4. The credit-guard / cost dimension in Mode A makes hard-escalate net-negative on the cost axis without a measured quality offset.

The IDEA stays documented in the dedicated memory ([[edge-cascade-tier3-repair-from-candidate]] is the closest related node; hard-escalate is a sibling, sharing the same "Tier 3 should be smarter about caps" intuition that Phase 0 weakened) so a future revisit can find this analysis.

## Re-trigger conditions (when to revisit)

Re-evaluate hard-escalate as a build candidate **if any of the following**:
- A future cascade change drops the FIRED sub-pool fresh-GPU resolution below ~0.50 (i.e. skip-repair stops carrying most of the slack). Likely cause: model downgrade, harder task mix, topology change.
- A genuine cost-effective Tier 3 stand-in for Mode A becomes available (e.g. a local-but-stronger model like `qwen3-coder:32b` at $0 marginal) that flips the cost arithmetic.
- Calibration moves the floor (e.g. a CP-5 or PD-1 v3 result that argues for a much lower or higher 0.30 floor); the headroom analysis above is calibrated against the current 0.30 floor + 0 FP on correct code.
- A user-facing policy change wants paid-cloud escalation to be MORE aggressive (e.g. for quality-first product positioning) — that's a product call, not a measurement call, and would override this decision.

## What unblocks next

- Update [[edge-cascade-open-threads]]: mark #6b (hard-escalate ADR) CLOSED with this doc, remove from actionable I3·S3 row. Top of stack moves to #5a Dashboard Phase B polish (the decaying WIP stash) or the #7 doc-only items.
- The dedicated memory [[edge-cascade-tier3-repair-from-candidate]] now has TWO sibling defer/shelve decisions (Phase 0 from #77, hard-escalate from this doc). Both are evidence-grounded; both have explicit re-trigger conditions.
- No code change. The skip-repair lever (`CONFIG.skip_repair_on_degen=True` default) remains the cascade's response to degen-trips; this decision pins that as the production answer.

## Related work

- `docs/FINDINGS-pd1-v2-skip-repair.md` (#75, `6833cde`) — the lever this decision says is sufficient; SHIPPED default-on via #76.
- `docs/FINDINGS-pd1-v2-warn-prompt.md` (#69, `cd7eee4`) — the lever this decision says doesn't work; REVERTED via #70.
- `docs/FINDINGS-pd1-v2-calibration.md` (#66) — the floor calibration this decision is built on.
- `docs/FINDINGS-tier3-repair-from-candidate-phase0.md` (#77, `16c6bb8`) — the cap-rate finding that bounds the headroom for any Tier-3-side intervention.
- [[edge-cascade-tier3-repair-from-candidate]] — dedicated memory; sibling defer.
- [[experiment-protocol-evidence-branches]] — why this doc isn't a FINDINGS doc (no new measurement; it's a synthesis of existing findings into a decision).
