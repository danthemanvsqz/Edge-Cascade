# Vinyl — build-target policy (READ FIRST)

You are building **Vinyl**: a standalone **TypeScript** library (streaming
async JSX server components, htmx-driven, DB-only state). This directory
(`projects/vinyl/`) is the **only** thing you work on.

## Vinyl is the target; edge-cascade is the factory — do not conflate them

This project lives *inside* the edge-cascade repo (embedded via `git subtree`)
purely so the artifact and its build telemetry travel together — "the machine
that built the machine." That co-location is **not** an invitation to treat
edge-cascade as your project.

- **NEVER edit anything outside `projects/vinyl/`.** No edits to `../../cascade/`,
  `../../mcp_servers/`, `../../*.py`, `../../RUNBOOK.md`, `../../CLAUDE.md`, CI,
  or any host file.
- **NEVER write edge-cascade host config.** Specifically: do not create or
  modify `../../.claude/settings.json` or any `../../.claude/*`. If a Claude
  setting is ever needed, it belongs under `projects/vinyl/.claude/`, never the
  host root. (A prior run wrongly wrote host MCP permissions there — do not
  repeat that.)
- edge-cascade's `runs/*.rec` is **generation telemetry only**. It is not
  Vinyl state and is not yours to curate from inside the build.

## The correctness gate is Vinyl's OWN toolchain — NOT the Python cascade gate

`edge-verify` / `checks.dsl` / `validate_log.py` parse and **exec Python**.
Vinyl is **TypeScript**. They cannot validate Vinyl and will mis-report.

- **Do NOT call `edge-verify.verify_syntax` / `verify_functional` on Vinyl code.**
- A milestone is "green" only when ALL of these pass, run from
  `projects/vinyl/`:
  - `npm run test:run`   (vitest)
  - `npm run typecheck`  (tsc --noEmit)
  - `npm run lint`       (eslint --max-warnings=0)
- You MAY use `edge-npu` / `edge-gpu` to *draft* TypeScript. Drafts are
  unverified until the three commands above pass. The cascade tiers generate;
  they do not certify Vinyl.

## Build discipline

- Follow [`PLAN.md`](./PLAN.md) — locked decisions and the M0–M7 order. Do not
  re-litigate locked decisions; build them.
- One milestone at a time; end each green (the three commands) and commit
  inside `projects/vinyl/` with a `M<n>: …` message. Do the **htmx-ws spike
  before M4** and record findings in [`ARCHITECTURE.md`](./ARCHITECTURE.md).
- Local-first per the edge-cascade mesh policy; the paid `edge-cloud` tier
  stays off. Honesty rule still applies: never claim a tier ran unless its
  `runs/<server>.rec` actually grew.
