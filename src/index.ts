/**
 * Vinyl — streaming server-rendered async JSX, htmx-driven. Zero client state.
 *
 * M1 surface: the vnode model + automatic JSX runtime. Renderer (M2),
 * Suspense/ErrorBoundary (M3), WS transport (M4), actions/live regions (M5)
 * land next. See ARCHITECTURE.md.
 */
export {
  Fragment,
  h,
  createVNode,
  isVNode,
  flattenChildren,
  normalizeProps,
} from "./vnode.js";
export type {
  VNode,
  VNodeChild,
  RawChild,
  Props,
  Component,
  VNodeType,
  FragmentType,
} from "./vnode.js";
export { jsx, jsxs } from "./jsx-runtime.js";
