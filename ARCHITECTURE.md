# ARCHITECTURE — Vinyl

> Server-side rendering is the vinyl record of web tech: declared dead by the
> SPA era, back now warmer/cooler, and the kids are driving the revival.

This document is the durable record of **what Vinyl is** and **what was
decided**. The decisions in §2 are locked by the user and are reproduced
verbatim from `PLAN.md`. Do not re-litigate them — build to them.

## 1. Goal

A focused **Node library** where you author **async JSX server components**;
the engine renders them to HTML and **pushes updates over a WebSocket as
`hx-swap-oob` frames**, with **htmx** applying them. State lives in the DB;
the server re-renders from it. It is Rails/Django's "fat model, HTML over the
wire" with React's composition model and LiveView-style push — as a small
bring-your-own-router/db module. **No client state store. Ever.**

## 2. Locked decisions (do NOT revisit)

1. **JSX** authoring (not tagged-template, not hyperscript). We ship our own
   automatic JSX runtime; JSX is one front-end that produces vnodes. A
   hyperscript `h` exists *under the hood* so the engine is testable without a
   transpile, but the authored DX is JSX.
2. **WebSocket is the primary transport.** Initial paint is one normal HTTP
   route streaming the shell; WS owns all updates thereafter. SSE / long-poll
   / HTTP-chunked are possible *later as adapters*, not now.
3. **Focused library**, not a framework. No bundled router/session/db/scaffold.
   Thin adapters only (`node:http`, Express, Fastify upgrade wiring).
4. **State source of truth is the DB.** A render is a pure function of (DB
   state, action input). No authoritative in-memory or client state.
5. Defaults (flag-able, but start here): **TypeScript** (ship types), **Node
   ≥ 20**, **vitest**, **tsup** build, demo DB **better-sqlite3**, **htmx
   2.x**.

## 3. Architecture

1. **JSX runtime.** Own automatic runtime (`jsx`, `jsxs`, `Fragment`) via
   `jsxImportSource`. JSX → vnodes `{ type, props, children }`; `type` is a
   tag string or a (possibly async) component fn.
2. **Streaming renderer.** `renderToStream(vnode, ctx) → AsyncIterable<string>`.
   Intrinsic tags → escaped attributes (`hx-*`/`ws-*` pass through) + escaped
   text (XSS-safe default; explicit raw escape hatch) + void-element handling;
   components invoked. Backpressure-aware (await drain).
3. **Async components = Suspense over OOB (the novel core).** A component
   returning a Promise emits its `<Suspense>` fallback with a **stable id**
   inline, keeps streaming siblings, and on resolve pushes an `hx-swap-oob`
   frame targeting that id. Out-of-order, progressive,
   slow-never-blocks-fast. `<ErrorBoundary>`: a rejected async subtree renders
   its fallback and pushes — never kills the stream/socket.
4. **WebSocket transport.** One HTTP route streams the shell
   (`<body hx-ext="ws" ws-connect="/ws">`); WS owns updates. Inbound: htmx
   `ws-send`/triggered forms → message → **action handler** (user code,
   reads/writes DB) → re-render affected regions → OOB frames out.
   Per-connection context: DB handle, authed user (resolved at WS upgrade),
   the push fn.
5. **State model.** No client state. `liveRegion(key)` + `signal(key)`
   re-render+push a bound subtree when its data changes (pub/sub or DB hook).
   Interactions are server round-trips — the CRUD/forms/dashboard sweet spot;
   explicitly NOT a low-latency canvas. Document that boundary.

## 4. Public API (small)

`jsx-runtime`; `Suspense`, `ErrorBoundary`; `renderToStream` /
`renderToString`; `createWSServer` / `handleUpgrade`; `defineAction` + message
router; `oob()` framing; `liveRegion` / `signal`; server-scoped context API;
adapters `node:http` / Express / Fastify.

## 5. Milestones (build order — each ends green: tests + lint)

- **M0** — Skeleton: ESM pkg, Node ≥ 20, TS, tsup, vitest, lint, CI; this doc.
- **M1** — JSX runtime + vnode model.
- **M2** — Sync renderer → string/stream.
- **M3** — Async components + Suspense/ErrorBoundary (the core).
- **M4** — WS transport + htmx OOB framing (gated by the htmx-ws spike, §7).
- **M5** — Actions + live regions (the state model); TodoMVC proof.
- **M6** — Adapters + DX + docs; Playwright e2e with real htmx + browser.
- **M7** — Hardening: backpressure, fault injection, security, perf.

## 6. Risks / sharp edges

- **htmx ws-extension contract = the integration risk.** A small spike (§7)
  gates M4: design framing to observed behaviour, not assumptions.
- **Async-component semantics are ours.** Lock to `vnode | Promise<vnode>` +
  `<Suspense>` first. AsyncIterable/generator components = stretch goal.
- **Stable OOB ids** must be deterministic and collision-free across
  re-renders — path-based + user-overridable key. Designed M3, reused M5.
- **WS backpressure**: many live regions → coalesce/debounce per connection.
  Hooks designed M4; enforced M7.
- **No client state ⇒ every interaction is a round-trip.** Acceptable for the
  target domain (CRUD/forms/dashboards). Not for canvas/drag-latency.

## 7. htmx-ws spike findings

> **Status: PENDING.** Run before M4 (see `PLAN.md` Risks). Stand up htmx 2.x
> `hx-ext="ws"`, hand-push a frame, and record here *exactly* how inbound HTML
> is applied: `hx-swap-oob` semantics, id targeting, multiple OOB elements per
> frame, and whether the WS extension wraps/unwraps anything. M4's `oob()`
> framing is designed to these observations, not to assumptions.
