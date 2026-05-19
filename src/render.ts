/**
 * M2 + M3 renderer.
 *
 * M2: synchronous, XSS-safe HTML rendering of a vnode tree.
 * M3 (the novel core): async components + <Suspense>/<ErrorBoundary>.
 *
 *   - A component may return a Promise. Wrapped in <Suspense>, its boundary
 *     emits the fallback inline behind a STABLE id, streaming continues, and
 *     when the subtree resolves an out-of-band frame for that id is pushed.
 *   - Out-of-order: boundaries flush as they resolve (Promise.race drain),
 *     so a slow boundary never blocks a fast sibling.
 *   - <ErrorBoundary> is structural: if its subtree rejects, its fallback
 *     replaces that subtree — the stream/socket is never killed.
 *
 * `renderToString` stays strictly synchronous (async is a streaming concept):
 * a pending async subtree throws, directing the caller to `renderToStream`.
 *
 * OOB FRAMING IS PROVISIONAL. The `<vinyl-slot id hx-swap-oob>` wrapper below
 * is a placeholder until the htmx-ws spike (ARCHITECTURE.md §7, gates M4)
 * pins the exact hx-swap-oob contract. Only this constant + the two template
 * literals need to change.
 */
import { Fragment, isVNode, isRaw } from "./vnode.js";
import type { Props, VNode } from "./vnode.js";
import { Suspense, ErrorBoundary } from "./suspense.js";
import type { SuspenseProps, ErrorBoundaryProps } from "./suspense.js";
import { boundaryId, childPath } from "./ids.js";

const BOUNDARY_TAG = "vinyl-slot";

export const VOID_ELEMENTS: ReadonlySet<string> = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "param", "source", "track", "wbr",
]);

export function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function escapeAttr(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function serializeAttrs(props: Props): string {
  let out = "";
  for (const key of Object.keys(props)) {
    if (key === "children" || key === "key") continue;
    const value = props[key];
    const name =
      key === "className" ? "class" : key === "htmlFor" ? "for" : key;
    if (value === true) {
      out += ` ${name}`;
    } else if (value === false || value == null) {
      continue;
    } else if (typeof value === "string") {
      out += ` ${name}="${escapeAttr(value)}"`;
    } else if (typeof value === "number") {
      out += ` ${name}="${escapeAttr(String(value))}"`;
    }
    // object / function / symbol / bigint → skipped
  }
  return out;
}

export interface RenderContext {
  [key: string]: unknown;
}

function toCtx(ctx?: RenderContext): RenderContext {
  return ctx ?? {};
}

/** Sentinel: a sync walk hit an async component. Caught by the nearest
 * <Suspense> (streaming) or surfaced as a friendly error (renderToString). */
const PENDING: unique symbol = Symbol("vinyl.pending");

function isThenable(v: unknown): v is PromiseLike<unknown> {
  return (
    (typeof v === "object" || typeof v === "function") &&
    v !== null &&
    typeof (v as { then?: unknown }).then === "function"
  );
}

function slot(id: string, inner: string, oob: boolean): string {
  const attr = oob ? ` hx-swap-oob="true"` : "";
  return `<${BOUNDARY_TAG} id="${id}"${attr}>${inner}</${BOUNDARY_TAG}>`;
}

interface Walk {
  path: string;
  ctx: RenderContext;
  /** present ⇒ streaming: deferred boundaries register here. */
  sched: Scheduler | null;
}

/**
 * Synchronous core. Yields HTML chunks. Throws PENDING when it meets an
 * async component with no enclosing <Suspense> able to absorb it.
 */
function* walkSync(node: unknown, idx: number, st: Walk): Generator<string> {
  if (node == null || typeof node === "boolean") return;

  if (typeof node === "string" || typeof node === "number") {
    yield escapeText(String(node));
    return;
  }

  if (isRaw(node)) {
    yield node.html;
    return;
  }

  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) yield* walkSync(node[i], i, st);
    return;
  }

  if (!isVNode(node)) return;

  const type = node.type;
  const here = childPath(st.path, idx);

  if (type === Fragment) {
    yield* walkChildrenSync(node, here, st);
    return;
  }

  if (type === Suspense) {
    yield suspenseString(node, idx, st);
    return;
  }

  if (type === ErrorBoundary) {
    // Sync path: an EB with only sync children is transparent. If its subtree
    // is async it must sit under a <Suspense>; PENDING propagates to it.
    yield* walkChildrenSync(node, here, st);
    return;
  }

  if (typeof type === "function") {
    const result: unknown = type({ ...node.props, children: node.children });
    if (isThenable(result)) throw PENDING;
    yield* walkSync(result, idx, { ...st, path: here });
    return;
  }

  // intrinsic tag
  const tag = type;
  yield `<${tag}${serializeAttrs(node.props)}>`;
  if (VOID_ELEMENTS.has(tag)) return;
  yield* walkChildrenSync(node, here, st);
  yield `</${tag}>`;
}

function* walkChildrenSync(
  node: VNode,
  here: string,
  st: Walk,
): Generator<string> {
  const kids = node.children;
  for (let i = 0; i < kids.length; i++) {
    yield* walkSync(kids[i], i, { ...st, path: here });
  }
}

/**
 * Single source of truth for a <Suspense> boundary, used by both the sync
 * shell walk and the async subtree renderer. Probes the subtree
 * synchronously: fully sync ⇒ transparent inline render; a pending async ⇒
 * inline fallback behind a stable id now + a deferred OOB frame registered
 * on the scheduler. Computes the id from the *real* child index so sibling
 * and nested boundaries never collide.
 */
function suspenseString(node: VNode, idx: number, st: Walk): string {
  const props = node.props as SuspenseProps;
  const key = typeof props.key === "string" ? props.key : null;
  const id = boundaryId(st.path, idx, key);
  const here = childPath(st.path, idx);

  const buf: string[] = [];
  try {
    for (const c of walkChildrenSync(node, here, st)) buf.push(c);
  } catch (e) {
    if (e !== PENDING) throw e;
    if (!st.sched) {
      throw new Error(
        "vinyl: async component inside <Suspense> requires renderToStream " +
          "(renderToString is synchronous)",
      );
    }
    const sched = st.sched;
    sched.register(async () =>
      slot(id, await renderAsyncSubtree(node, here, st), true),
    );
    return slot(id, renderSyncToString(props.fallback, st), false);
  }
  // Fully synchronous Suspense → transparent (no wrapper, no fallback).
  return buf.join("");
}

function renderSyncToString(node: unknown, st: Walk): string {
  let out = "";
  for (const c of walkSync(node, 0, st)) out += c;
  return out;
}

/**
 * Async render of a subtree to a single HTML string. Awaits component
 * promises. Nested <Suspense> registers its own deferred frame on the same
 * scheduler (progressive). <ErrorBoundary> is structural: a rejecting
 * subtree is replaced by its fallback in place.
 */
async function renderAsyncNode(
  node: unknown,
  idx: number,
  st: Walk,
): Promise<string> {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") {
    return escapeText(String(node));
  }
  if (isRaw(node)) return node.html;
  if (Array.isArray(node)) {
    let out = "";
    for (let i = 0; i < node.length; i++) {
      out += await renderAsyncNode(node[i], i, st);
    }
    return out;
  }
  if (!isVNode(node)) return "";

  const type = node.type;
  const here = childPath(st.path, idx);

  if (type === Fragment) {
    return renderAsyncChildren(node, here, st);
  }

  if (type === Suspense) {
    // Same boundary semantics as the shell walk (correct child index).
    return suspenseString(node, idx, st);
  }

  if (type === ErrorBoundary) {
    const props = node.props as ErrorBoundaryProps;
    try {
      return await renderAsyncChildren(node, here, st);
    } catch (err) {
      const fb =
        typeof props.fallback === "function"
          ? props.fallback(err)
          : props.fallback;
      return renderSyncToString(fb, st);
    }
  }

  if (typeof type === "function") {
    const result: unknown = await type({
      ...node.props,
      children: node.children,
    });
    return renderAsyncNode(result, idx, { ...st, path: here });
  }

  const tag = type;
  const open = `<${tag}${serializeAttrs(node.props)}>`;
  if (VOID_ELEMENTS.has(tag)) return open;
  return `${open}${await renderAsyncChildren(node, here, st)}</${tag}>`;
}

async function renderAsyncChildren(
  node: VNode,
  here: string,
  st: Walk,
): Promise<string> {
  const kids = node.children;
  let out = "";
  for (let i = 0; i < kids.length; i++) {
    out += await renderAsyncNode(kids[i], i, { ...st, path: here });
  }
  return out;
}

/** Render a <Suspense> boundary's *children* fully (the resolved content). */
function renderAsyncSubtree(
  node: VNode,
  here: string,
  st: Walk,
): Promise<string> {
  return renderAsyncChildren(node, here, st);
}

interface Settled {
  p: Promise<Settled>;
  html: string;
}

/** Out-of-order drain: boundaries flush as they resolve. */
class Scheduler {
  private readonly set = new Set<Promise<Settled>>();

  register(produce: () => Promise<string>): void {
    let p!: Promise<Settled>;
    p = produce().then(
      (html) => ({ p, html }),
      // produce() handles its own errors; defensive empty frame.
      () => ({ p, html: "" }),
    );
    this.set.add(p);
  }

  get size(): number {
    return this.set.size;
  }

  async next(): Promise<string> {
    const s = await Promise.race(this.set);
    this.set.delete(s.p);
    return s.html;
  }
}

export function renderToString(node: unknown, ctx?: RenderContext): string {
  const st: Walk = { path: "", ctx: toCtx(ctx), sched: null };
  try {
    return renderSyncToString(node, st);
  } catch (e) {
    if (e === PENDING) {
      throw new Error(
        "vinyl: async components require <Suspense> + renderToStream " +
          "(renderToString is synchronous in M2/M3)",
      );
    }
    throw e;
  }
}

export function renderToStream(
  node: unknown,
  ctx?: RenderContext,
): AsyncIterable<string> {
  const context = toCtx(ctx);
  return {
    async *[Symbol.asyncIterator]() {
      const sched = new Scheduler();
      const st: Walk = { path: "", ctx: context, sched };
      // 1. Shell: stream everything sync, registering deferred boundaries.
      try {
        for (const chunk of walkSync(node, 0, st)) yield chunk;
      } catch (e) {
        if (e === PENDING) {
          throw new Error(
            "vinyl: an async component must be wrapped in <Suspense>",
          );
        }
        throw e;
      }
      // 2. Drain deferred boundaries out-of-order as they resolve. A frame
      //    may itself register nested boundaries; keep going until empty.
      while (sched.size > 0) {
        yield await sched.next();
      }
    },
  };
}
