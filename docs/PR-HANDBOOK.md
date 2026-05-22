# PR Handbook — the review cycle

The process for every edge-cascade PR the agent opens. It exists so the
review→fix loop is **bounded, honest, and spend-safe** — not a vibe.

## The cycle

1. **Open the PR on first push.** No deferral; the PR exists before any review.
2. **Fire the review job** — `pr_review.py <PR#> --post` (Opus 4.7,
   credit-guarded). The cascade build path stays **$0**; review spend is the one
   sanctioned lane, recorded to `runs/edge-review.rec`, capped by the guard.
3. **Triage every issue, out loud.** For each item the review raises, classify
   and *state the classification*:
   - **VALID** (real bug / security / invariant breach) → implement.
   - **NIT** (style/minor) → implement if cheap; otherwise note and skip.
   - **DUBIOUS / HALLUCINATED** (wrong about the code, references things not in
     the diff, contradicts a locked decision) → **do NOT implement.** Verify
     against the actual code (`file:line`), then **reply with the concern for
     the user's review** — don't "fix" a non-problem, and don't silently drop
     it either.
4. **Implement the valid items**, commit, push.
5. **Repeat on the new diff** — re-review — **until termination** (below).

## Termination (bounded — this is the spend guard)

Stop the cycle when **either**:
- the review verdict is **APPROVE** (no valid REQUEST CHANGES items remain), or
- **`review_max_rounds` (default 3)** reviews have run on this PR.

Then **summarize**: what was fixed, what was rejected and why, the per-PR review
spend. Never loop unbounded — runaway review→fix→review is the failure mode this
cap prevents (it's real money and it must terminate).

**Don't re-review trivial pushes.** A doc/comment-only or rename commit carries
no code risk; re-running a paid review on it is waste. Re-review applies to
substantive code changes. (Judgment call — state it when skipping.)

## Hallucination handling (the trust check)

Opus reviews are advisory, not authoritative. It can be wrong about: code it
can't see (truncated diff), project conventions, anything outside the diff.
Before acting on a suggestion, **verify it against the real code.** If it's
wrong, reject it *with evidence* and surface it to the user — the user is the
arbiter when Opus and the agent disagree.

## Invariants the cycle must never break

- **Cascade build path stays $0.** Review is the only sanctioned spend, bounded
  by the credit guard + the round cap.
- **`cascade/` keeps 100% coverage** after every fix.
- **No fix may break a locked decision** (the Celery-readiness charter, the
  spend invariant) without explicitly flagging it for the user first.

## Enforcement status (honest)

Steps that are **judgment** (triage, implement, reject-with-evidence) are the
agent's to do and can't be automated — automation can't decide if a suggestion
is valid. Steps that are **mechanical and spend-bearing** (firing the review
every time; terminating the loop within budget) are where manual discipline is
< 100% reliable across sessions, so they get **automated guards** (see
`pr_review.py`'s per-PR round cap and the optional `scripts/ship.py`). If a
guard is missing, this handbook is aspirational for that step — say so.
