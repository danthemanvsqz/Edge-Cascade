/**
 * M2 — synchronous server-side HTML renderer over the vnode tree.
 *
 * XSS-safe by default: text and attribute values are escaped. The only way
 * to emit unescaped markup is the explicit `raw()` escape hatch. One sync
 * generator core (`renderChunks`) backs both `renderToString` (join) and
 * `renderToStream` (pull-model AsyncIterable → backpressure-aware).
 *
 * Async components / Suspense / ErrorBoundary are M3; a component returning
 * a thenable throws here by design so the milestone boundary stays sharp.
 */
import { Fragment, isVNode, isRaw } from "./vnode.js";
import type { Props } from "./vnode.js";

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

/**
 * Serialize props to an attribute string. `className`/`htmlFor` are the only
 * JSX-isms mapped (→ `class`/`for`); everything else (`hx-*`, `ws-*`,
 * `data-*`, `aria-*`, …) is emitted verbatim. `true` → boolean attribute;
 * `false`/`null`/`undefined` → omitted; non-string/number values skipped.
 */
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

function* renderChunks(
  node: unknown,
  ctx: RenderContext,
): Generator<string> {
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
    for (const child of node) yield* renderChunks(child, ctx);
    return;
  }

  if (isVNode(node)) {
    const type = node.type;

    if (type === Fragment) {
      for (const child of node.children) yield* renderChunks(child, ctx);
      return;
    }

    if (typeof type === "function") {
      const result: unknown = type({ ...node.props, children: node.children });
      if (
        typeof result === "object" &&
        result !== null &&
        typeof (result as { then?: unknown }).then === "function"
      ) {
        throw new Error(
          "vinyl: async components require <Suspense> (M3); " +
            "renderToString/renderToStream are sync in M2",
        );
      }
      yield* renderChunks(result, ctx);
      return;
    }

    // narrowed: type is a string intrinsic tag
    const tag = type;
    yield `<${tag}${serializeAttrs(node.props)}>`;
    if (VOID_ELEMENTS.has(tag)) return;
    for (const child of node.children) yield* renderChunks(child, ctx);
    yield `</${tag}>`;
    return;
  }
  // unknown node kind → emit nothing
}

export function renderToString(node: unknown, ctx?: RenderContext): string {
  let out = "";
  for (const chunk of renderChunks(node, toCtx(ctx))) out += chunk;
  return out;
}

export function renderToStream(
  node: unknown,
  ctx?: RenderContext,
): AsyncIterable<string> {
  const c = toCtx(ctx);
  return {
    async *[Symbol.asyncIterator]() {
      for (const chunk of renderChunks(node, c)) yield chunk;
    },
  };
}
