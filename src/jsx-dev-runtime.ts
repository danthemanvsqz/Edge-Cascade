/** Development JSX runtime entry (`jsxImportSource` in dev). Same vnode
 * output as the production runtime; the extra dev args are accepted and
 * ignored (no client VDOM, so source/self carry no runtime meaning). */
import { createVNode, Fragment } from "./vnode.js";
import type { Props, VNode, VNodeType } from "./vnode.js";

export { Fragment };
export type { VNode };

export function jsxDEV(
  type: VNodeType,
  props: Props,
  key?: string,
  _isStaticChildren?: boolean,
  _source?: unknown,
  _self?: unknown,
): VNode {
  return createVNode(type, props, key ?? null);
}
