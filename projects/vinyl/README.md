# Vinyl

**Streaming server-rendered async JSX, htmx-driven. Zero client state.**

Server-side rendering is the vinyl record of web tech: declared dead by the
SPA era, back now warmer and cooler, and the kids are driving the revival.

You author async JSX server components. The engine streams them to HTML and
pushes updates over a WebSocket as `hx-swap-oob` frames; **htmx** applies them.
State lives in your database; the server re-renders from it. Rails/Django's
"fat model, HTML over the wire" with React's composition model and
LiveView-style push — as a small, bring-your-own-router/db module.

> **No client state store. Ever.**

## Status

Early development. Built milestone by milestone — see
[`ARCHITECTURE.md`](./ARCHITECTURE.md) for the locked decisions and
[`PLAN.md`](./PLAN.md) for the build order. Not yet published to npm.

- **Node ≥ 20**, ESM-only, TypeScript types shipped.
- Defaults: vitest, tsup, htmx 2.x, better-sqlite3 (demo only).

## Provenance

Built by **Edge-Cascade — the machine that built the machine.** Vinyl is the
machine; the local inference mesh that constructs it is the one that built it.
This source lives *inside* the factory (`projects/vinyl/`, embedded via
`git subtree`, full M0→ history intact) so the artifact and its build record
travel together. Per-run build telemetry (the `.rec` recorder, replayed and
reconciled) is committed under [`../../evidence/`](../../evidence/).

## License

MIT
