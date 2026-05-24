# Prioritization Protocol

How work on this project is prioritized: a **4×4 Impact × Severity matrix**. The
agent (Claude) uses this to choose what to do next and to order any backlog it
proposes. Source: user directive, 2026-05-24. Companion: the agent's
`prioritization-protocol` memory.

## Axes

**Impact** — columns, left→right — how much lift the work delivers:

| | meaning |
|---|---|
| **I1 · Trivial** | cosmetic only — lint, nitpicks, style churn |
| **I2 · Minor** | small convenience/cleanup; real but limited value |
| **I3 · Major** | meaningful feature, sizeable bug fix, real new capability |
| **I4 · Critical** | critical bug fixes, major features, anything blocking |

**Severity** — rows, top→bottom — engineering **risk/effort**: how large,
uncertain, or breakage-prone the change is:

| | meaning |
|---|---|
| **S1 · Safe** | small, well-understood, isolated |
| **S2 · Low** | modest, mostly understood |
| **S3 · Moderate** | sizeable or some unknowns; needs care |
| **S4 · Severe** | large/risky/high blast-radius; outcome uncertain |

## The matrix

```
 Severity ↓ \ Impact →   I1 Trivial   I2 Minor   I3 Major   I4 Critical
 S1 Safe                  ✗ never        #7         #4          #1
 S2 Low                   ✗ never        #8         #5          #2
 S3 Moderate              ✗ never        #9         #6          #3
 S4 Severe (risky)        ✗ never      ⏳ park     ⏳ park      ⏳ park
```

`✗` never · `⏳` park + de-risk · `#n` actionable, in priority order.

## Zones

1. **Never — the entire lowest-impact column (I1), any severity.**
   Lint, nitpicks, style. Not worth doing *or even tracking* — dropped. The
   `I1·S4` corner is **Never, not Park**: low impact isn't worth de-risking.

2. **Park + de-risk — the highest-severity row (S4), for impact ≥ I2.**
   Too risky to execute now, but **kept tracked**. We actively whittle the risk
   down — decompose into smaller pieces, spike/prototype the unknown, add tests
   or guardrails that shrink the blast radius — until the severity drops to
   **≤ S3**, at which point the item **re-enters the actionable zone** at its
   impact column. A parked item is re-scored after each de-risk step.

3. **Actionable — the 9 remaining cells (I2–I4 × S1–S3).**
   Executed in the order shown (`#1`…`#9`).

## Ordering rule

**Impact descending first, then severity ascending** (safest / quick-win first).
So `#1 = I4·S1` (critical + safe) through `#9 = I2·S3`. At equal impact, the
**less risky** item goes first — a high-impact quick win beats an equally
high-impact risky big-bet.

## How to apply

When choosing the next task or proposing an order:

1. Classify each candidate as `(impact, severity)`.
2. **Drop** everything in the `I1` column.
3. **Park** every `S4` (impact ≥ I2) into a tracked de-risk list, and name the
   next concrete de-risk step for each.
4. Present the remaining items in `#1 → #9` order.
5. After any de-risk step on a parked item, re-score it; if it falls to `≤ S3`
   it joins the actionable order at its impact column.

This governs *what* and *in what order*. It pairs with the pacing rule (small,
reviewable increments — *how* each item is executed) and the metric priorities
(quality > cost > latency — what "impact" weighs).
