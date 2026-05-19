# PLAN — Vinyl: streaming server-rendered JSX components, htmx-driven

> **For a fresh session picking this up cold.** This is an approved
> implementation plan. The decisions in "Locked decisions" are *already made
> by the user* — do not re-litigate them; build it. Read "Grounding" first so
> you don't re-derive the concept or the prior art.

## Code name & identity

- **Code name: Vinyl.** Theme: server-side rendering is the vinyl record of
  web tech — declared dead by the SPA era, back now warmer/cooler, and the
  kids are driving the revival. Keep that voice in docs.
- Repo: `github.com/danthemanvsqz/vinyl` (confirmed free for the account).
- npm publish name: `@danthemanvsqz/vinyl` (scoped — bare `vinyl` is taken by
  Gulp's virtual-file-format pkg); unscoped fallback `vinyljs`. Publish name
  is an M0 detail, not a blocker — does not touch code.

## Goal

A focused **Node library** where you author **async JSX server components**;
the engine renders them to HTML and **pushes updates over a WebSocket as
`hx-swap-oob` frames**, with **htmx** applying them. State lives in the DB;
the server re-renders from it. It is Rails/Django's "fat model, HTML over the
wire" with React's composition model and LiveView-style push — as a small
bring-your-own-router/db module. **No client state store. Ever.**

## Locked decisions (do NOT revisit)

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

## Grounding — what this is, and prior art (don't reinvent)

Position *against* these; Vinyl is the gap none of them fill (a focused async
Node streaming module with htmx as the driver and zero client state):

- **React 18 streaming SSR + Suspense** (`renderToReadableStream`) — the
  async-flush semantics we mirror, but client-VDOM-bound.
- **Marko** — nearest cousin (streaming async components) but its own ecosystem.
- **Phoenix LiveView / Hotwire Turbo Streams** — server-push HTML, but
  framework-bound and not JSX.
- **htm** — JSX-without-build; we chose real JSX instead (build step accepted).

"React patterns" here = components, props, composition, `Fragment`,
`<Suspense>`, `<ErrorBoundary>`, server-scoped context, async components.
There is **no client virtual DOM** — vnodes are a server render tree only.

## Architecture

1. **JSX runtime.** Own automatic runtime (`jsx`, `jsxs`, `Fragment`) via
   `jsxImportSource`. JSX → vnodes `{ type, props, children }`; `type` is a
   tag string or a (possibly async) component fn.
2. **Streaming renderer.** `renderToStream(vnode, ctx) → AsyncIterable<string>`.
   Intrinsic tags → escaped attributes (`hx-*`/`ws-*` pass through) + escaped
   text (XSS-safe default; explicit raw escape hatch) + void-element handling;
   components invoked. Backpressure-aware (await drain).
3. **Async components = Suspense over OOB (the novel core).** A component
   returning a Promise emits its `<Suspense>` fallback with a **stable id**
   inline, keeps streaming siblings, and on resolve pushes an
   `hx-swap-oob` frame targeting that id. Out-of-order, progressive,
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
6. **Public API (small):** `jsx-runtime`; `Suspense`, `ErrorBoundary`;
   `renderToStream` / `renderToString`; `createWSServer` / `handleUpgrade`;
   `defineAction` + message router; `oob()` framing; `liveRegion` / `signal`;
   server-scoped context API; adapters `node:http` / Express / Fastify.

## Milestones (build order — each ends green: tests + lint)

- **M0 — Skeleton.** ESM pkg, Node ≥ 20, TS, tsup, vitest, lint, CI. Write
  `ARCHITECTURE.md` capturing the Locked decisions verbatim. Decide publish
  name. NO feature code.
- **M1 — JSX runtime + vnode model.** `jsx`/`jsxs`/`Fragment` automatic
  runtime; vnode shape; props normalization; children flattening; `h` for
  build-free tests. Unit tests.
- **M2 — Sync renderer → string/stream.** Attribute serialization (boolean
  attrs, `hx-*`/`ws-*` passthrough, escaping), text escaping, void elements,
  Fragment, sync components. `renderToStream` + `renderToString`,
  backpressure-aware. XSS-escaping tests are load-bearing.
- **M3 — Async components + Suspense/ErrorBoundary.** Inline fallback +
  **stable deterministic id** → resolved OOB frame; out-of-order flush;
  slow-doesn't-block-fast; rejected subtree → fallback + push. **The core —
  heaviest test coverage. Get the id scheme right here** (path-based +
  user-overridable `key`; collision-free across re-renders).
- **M4 — WS transport + htmx OOB framing.** `ws` server adapter; upgrade/auth
  hook; per-conn context; push fn; `oob()` framing; HTTP shell route →
  WS handoff. **Gated by the htmx-ws spike (see Risks) — do the spike
  first.**
- **M5 — Actions + live regions (the state model).** Inbound message router;
  `defineAction` handlers (read/write DB); `liveRegion`/`signal`. End-to-end
  demo: TodoMVC, state 100% in better-sqlite3, **zero client JS state** — the
  proof of thesis.
- **M6 — Adapters + DX + docs.** Express/Fastify upgrade wiring; optional
  esbuild `--import` dev loader (build-free `.jsx` in dev); consumer docs;
  Playwright e2e with **real htmx + a real browser** (proves htmx truly
  "drives the car").
- **M7 — Hardening.** WS backpressure (coalesce/debounce per connection);
  error-boundary fault injection; security pass (escaping fuzz, auth at
  upgrade + per-action authz); perf (push latency, memory under many conns).

## Risks / sharp edges

- **htmx ws-extension contract = the integration risk.** Before M4, run a
  *small spike*: stand up htmx 2.x `hx-ext="ws"`, hand-push a frame, confirm
  exactly how it applies inbound HTML (id / `hx-swap-oob` semantics). Design
  framing to observed behavior, not assumptions. **This spike gates M4.**
- **Async-component semantics are ours to define.** Lock to
  `vnode | Promise<vnode>` + `<Suspense>` first. AsyncIterable/generator
  components (incremental streaming *within* one component) = stretch goal,
  not early.
- **Stable OOB ids** for Suspense boundaries and live regions must be
  deterministic and collision-free across re-renders — path-based +
  user-overridable key. Designed in M3, reused in M5.
- **WS backpressure**: many live regions → coalesce/debounce per connection.
  Design the hooks in M4; enforce in M7.
- **No client state ⇒ every interaction is a round-trip.** Acceptable for the
  target domain (CRUD/forms/dashboards). State that boundary in docs; do not
  chase canvas/drag-latency use cases.

## How to start (cold session)

1. Read this file end to end.
2. `git init`; do **M0** (skeleton + `ARCHITECTURE.md` mirroring Locked
   decisions). Commit.
3. Execute **M1 → M7 in order**. Each milestone ends green (vitest + lint)
   and is committed before the next.
4. **Do the htmx-ws spike before M4** and write its findings into
   `ARCHITECTURE.md`.
5. Build via the edge-cascade Tier-3 mesh (local-first policy: NPU/GPU draft
   → verify → escalate; cloud is metered last-resort, credit-guarded). Trust
   `runs/*.rec` over narration; the dashboard's SPEND panel is informational
   once Tier 4 is wired.
