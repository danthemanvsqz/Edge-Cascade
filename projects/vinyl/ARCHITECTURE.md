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

> **Status: COMPLETE (2026-05-20).** htmx 2.0.4 + htmx-ext-ws 2.0.2.
> Evidence: the official sources, snapshotted in `spike/htmx-ws.ref.js` and
> `spike/htmx.ref.js`. Reproducible harness: `spike/serve.mjs` (`node
> spike/serve.mjs`, open `http://localhost:8787`).

### 7.1 The contract — derived from source

Inbound message handling lives in one block of the ws extension
(`spike/htmx-ws.ref.js:120-149`):

```js
var fragment = api.makeFragment(response)
if (fragment.children.length) {
  var children = Array.from(fragment.children)
  for (var i = 0; i < children.length; i++) {
    api.oobSwap(
      api.getAttributeValue(children[i], 'hx-swap-oob') || 'true',
      children[i],
      settleInfo,
    )
  }
}
api.settleImmediately(settleInfo.tasks)
```

`oobSwap` (`spike/htmx.ref.js:1466-1515`) does target selection and the actual
swap. From those two functions, the observed contract is:

1. **Every top-level element of a WS frame is treated as OOB.** The for-loop
   over `fragment.children` is unconditional; no envelope, no wrapping
   element. Top-level text/comment nodes are silently discarded (`children`
   skips non-elements).
2. **`hx-swap-oob` is optional on those top-level elements.** When absent it
   defaults to `'true'` (line 143). So `<x id="foo">…</x>` alone is enough to
   trigger an OOB swap against `#foo`. Emitting `hx-swap-oob="true"` is
   redundant-but-documentary.
3. **Default target is `#<id>` taken from the OOB element itself**
   (`htmx.ref.js:1468`). Default swap style is `outerHTML`
   (`htmx.ref.js:1470`). Both can be overridden:
   - `hx-swap-oob="<style>"` — same `#<id>` selector, different style.
   - `hx-swap-oob="<style>:<selector>"` — custom selector AND style. The
     selector follows htmx's extended syntax (`closest …`, `next …`, etc.).
4. **`hx-swap-oob` is stripped from the inserted node**
   (`htmx.ref.js:1479-1480`), so the wrapper tag goes into the live DOM
   without that attribute. The `id` survives.
5. **Multiple OOB elements per frame: fully supported and the design
   intent.** One WS message can carry any number of top-level elements; each
   is swapped independently in source order.
6. **No target → soft error, stream stays open.** If `#<id>` doesn't exist,
   htmx fires `htmx:oobErrorNoTarget` and removes the orphan
   (`htmx.ref.js:1510-1513`). Crucially: the socket is not closed and
   subsequent frames still apply. (Case 4 in the harness exercises this.)
7. **Settle runs once per frame, not per element**
   (`htmx-ws.ref.js:147`). Focus restoration, value preservation, and swap
   classes batch across all OOB elements in the same message — the server
   side does not call settle.
8. **No envelope, no wrapping by the ws extension itself.** The only mutation
   point before parsing is `extension.transformResponse(response, …)`
   (`htmx-ws.ref.js:133-135`), which only fires if some *other* extension
   registers it. The ws extension does not wrap, prefix, or re-encode the
   payload.
9. **Useful lifecycle hooks for later milestones:**
   `htmx:wsBeforeMessage` (cancellable, `htmx-ws.ref.js:126`),
   `htmx:wsAfterMessage` (`:148`), `htmx:oobBeforeSwap`
   (`htmx.ref.js:1496`), `htmx:oobAfterSwap` (`:1505`). M7 instrumentation
   should hook these, not invent a parallel observer.
10. **Single-document scope.** `oobSwap` queries against `getDocument()` by
    default (`htmx.ref.js:1467`); iframes/shadow-DOM are out of scope —
    matches React 18 streaming SSR's assumption.

### 7.2 Implications for M4

- **The M3 provisional wrapper is the right shape.**
  `<vinyl-slot id="…" hx-swap-oob="true">…</vinyl-slot>` is exactly what the
  contract expects. The `hx-swap-oob="true"` attribute is redundant but
  documents intent and is stripped on insertion anyway — keep it. Only the
  TWO template literals and the `BOUNDARY_TAG` constant in
  `src/render.ts` form the contract surface; nothing else needs to change.
- **`<vinyl-slot>` MUST be present on the initial shell with the same id.**
  Since the swap is `outerHTML`, the existing DOM node is *replaced*. M3 and
  M4 must emit the *same* wrapper element in the shell and in the OOB
  frame — id-only is not enough. (M3 already does this; this is the rule
  M4's `oob()` must preserve.)
- **`oob(id, html)` API is tiny.** It returns
  `` `<vinyl-slot id="${id}" hx-swap-oob="true">${html}</vinyl-slot>` ``.
  No protocol prefix, no envelope, no metadata. The scheduler concatenates
  whatever `oob(...)` strings it has into one WS message and calls
  `socket.send(message)`. This is the entire framing layer.
- **Batching for free.** The scheduler in `src/render.ts` may coalesce all
  boundaries that settle within one event-loop tick into a single frame;
  htmx will apply them in source order with one settle pass.
- **`onerror` / target-missing is non-fatal.** Vinyl's push fn doesn't need
  ack/nack — a stale id race resolves itself on the client. M5's
  `liveRegion`/`signal` re-pushes will re-target correctly after the next
  navigation.
- **Backpressure design hook (M4, enforced M7).** Each frame triggers a full
  settle. The scheduler's enqueue path is the natural place to coalesce
  superseded frames by id (drop earlier pending frames for the same id when
  a newer one is registered). Add the hook in M4; the actual debounce policy
  lands in M7 along with WS buffer-fullness checks.

### 7.3 Harness layout

- `spike/serve.mjs` — zero-dependency Node http server (port 8787) that
  serves a page wired with htmx 2.0.4 + ws-ext 2.0.2 from unpkg, accepts
  the WS upgrade, and hand-pushes four frames covering the contract:
  implicit OOB, explicit OOB, multi-element frame, missing-target.
- `spike/htmx-ws.ref.js` — verbatim snapshot of `bigskysoftware/htmx-
  extensions main/src/ws/ws.js` at fetch time. Line numbers above index this
  copy.
- `spike/htmx.ref.js` — verbatim snapshot of `bigskysoftware/htmx
  master/src/htmx.js` at fetch time. Same indexing convention.

The snapshots are the source of truth for the line-number citations above;
re-fetch them only when upgrading htmx, then re-validate any claims that
move.

## 8. M5 — actions + live regions (the state model)

> **Status: COMPLETE (2026-05-20).** `src/actions.ts`, `src/live.ts`, proof in
> `demo/todomvc/` + `test/m5-todomvc.test.ts`. Green: vitest + tsc + eslint.

The state model closes the loop opened by M4: inbound htmx frame → action →
DB write → live regions re-rendered *from the DB* → `hx-swap-oob` frames out.
The render is a pure function of (DB state); the browser holds no state.

### 8.1 Inbound — actions (`src/actions.ts`)

- `parseMessage(data)` splits an htmx `ws-send` JSON frame into
  `{ raw, input, headers }`: htmx tucks its metadata under a `HEADERS` key
  (`HX-Trigger`, `HX-Trigger-Name`, …, values string-or-null), everything else
  is form `input`.
- `createActionRouter({ actions, … })` returns the `onMessage` handler for
  `createWSServer`. It resolves an action name (default: a non-empty
  `input.action`, else `HEADERS["HX-Trigger-Name"]`; override via `nameFrom`)
  and dispatches to the matching `defineAction(name, handler)`.
- **The router never throws into the socket.** Parse failures, unknown actions,
  and handler exceptions route to overridable callbacks (`onParseError` /
  `onUnknown` / `onError`, defaulting to console) — one bad frame can't kill the
  connection. This is the M4 spike's "target-missing is non-fatal" stance
  carried to the inbound side.
- The handler gets an `ActionContext`: `conn`, `context` (= `conn.context`:
  DB handle + authed user from the upgrade), `input`, `headers`, `name`, and
  `refresh(...regions)` — re-render those regions and push ONE frame to the
  acting connection.

### 8.2 Outbound — live regions (`src/live.ts`)

- `liveRegion(key, render)` is a named, re-renderable subtree. `render(ctx)` is
  **synchronous** — the sweet spot is a server round-trip over a synchronous DB
  (better-sqlite3). Async data for the *initial* paint stays Suspense's job
  (M3); regions are for the post-action re-render + push.
- It reuses the M3 id scheme: `regionId(key)` = `vinyl-r-<safeSeg(key)>`, a
  distinct namespace from Suspense's `vinyl-s-*`. `mount(ctx)` emits the inline
  shell node `<vinyl-slot id>{content}</vinyl-slot>`; `frame(ctx)` emits the
  same id as an OOB swap via `oob()`. Same wrapper, same id in shell and
  frame — exactly the contract §7.2 requires.

### 8.3 Cross-connection — the signal hub

- §3.5 names `signal(key)`; the realization is `createSignalHub()`, a
  **per-server** pub/sub rather than a module global. Rationale: testability and
  multi-server isolation — a global channel registry leaks subscriptions across
  tests and across server instances. (This is an implementation choice for the
  §4 API sketch, not a change to a §2 locked decision.)
- `subscribe(key, conn, ...regions)` binds a connection's regions and returns
  an unsubscribe fn; `emit(key)` re-renders each subscriber's regions **against
  that subscriber's own context** and pushes one coalesced frame per
  connection; `remove(conn)` drops a connection's subscriptions (call it from
  `onClose`). An action mutates the DB then `emit`s — every connected tab
  updates. That fan-out is the "live" in live regions.

### 8.4 Proof of thesis — `demo/todomvc/`

TodoMVC with **state 100% in better-sqlite3 and zero client JS state**. `db.ts`
is the only writer; `app.ts` wires the regions, actions, router, and hub;
`server.ts` is a runnable entry (`node --import tsx demo/todomvc/server.ts`,
two tabs stay in sync). `test/m5-todomvc.test.ts` drives it over a real
`ws` socket against an in-memory sqlite DB: add/toggle/clear round-trips,
output escaping, multi-client broadcast, and subscription cleanup on close.
better-sqlite3 is a **demo** devDependency — the library itself stays
bring-your-own-db.
