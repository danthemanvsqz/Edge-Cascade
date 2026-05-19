/**
 * vnode model + hyperscript. JSX (M1 runtime) and `h` both funnel through
 * `createVNode`, so the engine is testable without a transpile step.
 *
 * A vnode is faithful: attribute names are NOT mapped here (`class`, `hx-*`,
 * `ws-*` stay verbatim) — serialization is the M2 renderer's concern.
 */

export const Fragment = Symbol("vinyl.Fragment");
export type FragmentType = typeof Fragment;

const RAW: unique symbol = Symbol("vinyl.raw");

/** Pre-escaped HTML placed in the tree. The sole bypass of text escaping. */
export interface RawNode {
  [RAW]: true;
  html: string;
}

/** Explicit, opt-in unescaped HTML escape hatch (renderable as a child). */
export function raw(html: string): RawNode {
  return { [RAW]: true, html };
}

export function isRaw(value: unknown): value is RawNode {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as RawNode)[RAW] === true
  );
}

export type Props = Record<string, unknown>;
export type Component = (
  props: Props,
) => VNode | VNodeChild | Promise<VNode | VNodeChild> | null;
export type VNodeType = string | FragmentType | Component;

export interface VNode {
  type: VNodeType;
  props: Props;
  children: VNodeChild[];
  key: string | null;
}

/** A normalized, renderable child (post-flatten). */
export type VNodeChild = VNode | RawNode | string | number;
/** Accepted child input before flattening. */
export type RawChild = VNodeChild | boolean | null | undefined | RawChild[];

export function isVNode(value: unknown): value is VNode {
  if (typeof value !== "object" || value === null) return false;
  const has = (k: string) => Object.prototype.hasOwnProperty.call(value, k);
  return (
    has("type") &&
    has("props") &&
    has("key") &&
    Array.isArray((value as { children?: unknown }).children)
  );
}

/**
 * Recursively flatten arbitrarily nested arrays to a single level. Drop
 * `null`, `undefined`, `true`, `false`. Keep `string`, `number` (incl. `0`
 * and `""`) and VNodes unchanged — the renderer stringifies, not us.
 */
export function flattenChildren(raw: unknown): VNodeChild[] {
  const out: VNodeChild[] = [];
  const walk = (child: unknown): void => {
    if (Array.isArray(child)) {
      for (const c of child) walk(c);
    } else if (typeof child === "string" || typeof child === "number") {
      out.push(child);
    } else if (isVNode(child) || isRaw(child)) {
      out.push(child);
    }
    // null | undefined | boolean | other → dropped
  };
  walk(raw);
  return out;
}

/** Shallow copy of props with the reserved `children`/`key` keys removed. */
export function normalizeProps(props: Props | null | undefined): Props {
  const copy: Props = { ...(props ?? {}) };
  delete copy.children;
  delete copy.key;
  return copy;
}

export function createVNode(
  type: VNodeType,
  props: Props | null | undefined,
  key?: string | null,
): VNode {
  return {
    type,
    props: normalizeProps(props),
    children: flattenChildren(props?.children),
    key: key ?? null,
  };
}

/**
 * Classic hyperscript. Variadic `children` override `props.children` when
 * present. `key` is read from props via a type guard (props values are
 * `unknown`) and applied in both branches; an explicit array still flattens.
 */
export function h(
  type: VNodeType,
  props?: Props | null,
  ...children: RawChild[]
): VNode {
  const propKey =
    typeof props?.key === "string" ? props.key : null;
  if (children.length > 0) {
    return createVNode(type, { ...props, children }, propKey);
  }
  return createVNode(type, props, propKey);
}
