/** Automatic JSX runtime entry (`jsxImportSource`). Thin wrapper over the
 * vnode model: JSX is one front-end; `h` is the other. */
import { createVNode, Fragment } from "./vnode.js";
import type { Props, VNode, VNodeType } from "./vnode.js";

export { Fragment };
export type { VNode };

/** Element with 0 or 1 child (children passed via `props.children`). */
export function jsx(type: VNodeType, props: Props, key?: string): VNode {
  return createVNode(type, props, key ?? null);
}

/** Element with static multiple children. Identical semantics — `createVNode`
 * flattens whether children arrive as one value or an array. */
export const jsxs = jsx;
