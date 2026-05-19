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
  raw,
  isRaw,
} from "./vnode.js";
export type {
  VNode,
  VNodeChild,
  RawChild,
  RawNode,
  Props,
  Component,
  VNodeType,
  FragmentType,
} from "./vnode.js";
export { jsx, jsxs } from "./jsx-runtime.js";
export {
  renderToString,
  renderToStream,
  escapeText,
  escapeAttr,
  VOID_ELEMENTS,
} from "./render.js";
export type { RenderContext } from "./render.js";
export { Suspense, ErrorBoundary } from "./suspense.js";
export type { SuspenseProps, ErrorBoundaryProps } from "./suspense.js";
export { safeSeg, childPath, boundaryId } from "./ids.js";
