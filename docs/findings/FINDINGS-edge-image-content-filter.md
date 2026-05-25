# Findings — content-filter false positive halts the edge-image Tier-3 flow

> Status: **investigation / evidence** (2026-05-22). Captured to guide
> resiliency work, not to defeat a platform safety control. See the
> **Boundary** section.
>
> **One line:** the `edge-image` skill couples creative image generation to the
> **Tier-3 model's text generation** (prompt mediation + vision critique); on
> emotionally-themed prompts the Anthropic API returns
> `400 Output blocked by content filtering policy` and the **entire CLI session
> halts**. The block is on the model's *text output*, **not** on the image.

## Why this matters to edge-cascade

Tier 3 *is the model* (`CLAUDE.md`: the launched Claude is the cascade's ceiling
and integrator). A coarse gate on Tier-3's creative output is therefore a real
**capability boundary of the mesh**, not a cosmetic bug: any topology whose top
tier must reason about or describe affect-laden material can be halted mid-build
by a classifier that never reaches the actual intent. Half of the originating
exercise was a deliberate *creative-inference stress test*; this is its result.

## The evidence (load-bearing — this is why we trust the diagnosis)

Two failed interactive sessions, transcripts under
`~/.claude/projects/c--Users-danth-src/`:

- **`f8514707…jsonl`** — the `edge-image` skill was invoked with the prompt
  *"abstract art piece based on a personal story about Pearl Jam's 'Black'
  performed live at Bottlerock Napa Valley — catharsis, emerging from a dark
  place into euphoria…"*. The `400 Output blocked` fired on the **very next
  assistant turn, before any image was generated or `Read`**. The only thing
  attached to context was a `command_permissions` record — **no pixels**.
- **`99ad6bf8…jsonl`** — 482 messages; a programmatic scan found **zero image
  content blocks anywhere in the transcript**, yet the same `400` fired on a
  text-generation turn after a *"reason artistically"* request.
- The error string is literally **"Output blocked"** — i.e. the model's
  *generation* was stopped, not an input image rejected.

## Diagnosis

A sentiment/safety classifier on **model output** false-positives on benign —
indeed *uplifting* — creative themes (loss, "Black", "dark place", catharsis).
It pattern-matches distress and stops **before** the arc resolves ("…but in the
end it was a wonderful ending"). It is coarse sentiment analysis over the prompt
context, not an assessment of the full narrative or actual intent.

**Corollary (kills the intuitive fix):** "save the PNG to disk first so raw
pixels never enter the sandbox" cannot help — the pixels were never the trigger.
The block is *upstream* of the image, on the model's text. Handling the source
image was never the boundary crossing it appeared to be.

## Implications for resiliency

1. **Architectural fragility — decouple creation from model-mediation.**
   Routing the user's creative intent through Tier-3 text generation (mediating
   it into an SDXL spec, then critiquing the result in prose) makes the whole
   workflow hostage to the output filter. The local SDXL server is the *user's*
   software on the *user's* hardware; the filter governs *model* output, not a
   user-driven local tool. A direct-drive path (user writes the prompt → POST to
   the server) renders the same art with the model out of the generation loop.
2. **No graceful degradation today.** A blocked model turn is a hard API error;
   there is **no Python `try/except` around the model's own inference**, so one
   block halts the entire session (and, once flagged content sits in context,
   every subsequent turn re-trips it — see `99ad6bf8`). Mitigation must be
   *architectural* (keep affect-laden generation off the critical path) plus
   *operational* (make vision-critique **opt-in**; let the user view their own
   PNGs in `runs/artifacts/` rather than the model re-describing them).
3. **Context poisoning.** Because the trigger persists in the conversation,
   recovery means a fresh session or trimming the offending context — another
   argument for not putting the creative theme in the model's mouth at all.

## Resiliency backlog (proposed, from this finding)

- [ ] `scripts/sdxl.py "<prompt>" [--seed --steps --size …]` — a thin
  direct-drive client for the local server (no model mediation in the path).
- [ ] Make the `edge-image` skill's **critique step opt-in**; default to
  reporting the artifact path + spec and letting the user look.
- [ ] Skill note: affect-heavy prompts may halt the Tier-3 turn; prefer the
  direct-drive path for those, and report false positives upstream.

## Boundary (explicit)

This is a **robustness** finding. The team does **not** engineer or reword
prompts to slip content past the content filter — that holds even where we judge
it to be misfiring. "Resilience" here means: (a) not coupling a legitimate local
creative workflow to a fragile model-output step, (b) failing gracefully instead
of halting, and (c) **reporting false positives upstream** (Claude Code
thumbs-down / feedback) as the correct channel for a misclassification.
