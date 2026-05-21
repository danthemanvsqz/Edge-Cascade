/**
 * Vinyl — streaming server-rendered async JSX, htmx-driven. Zero client state.
 *
 * Through M5: vnode model + automatic JSX runtime (M1), streaming HTML
 * renderer (M2), Suspense/ErrorBoundary with OOB framing (M3), WS transport
 * + shell handoff (M4), actions + live regions / signals (M5).
 * See ARCHITECTURE.md.
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
export { oob, BOUNDARY_TAG } from "./oob.js";
export { createWSServer } from "./ws.js";
export type {
  VinylConnection,
  CreateWSServerOptions,
  VinylWSServer,
} from "./ws.js";
export { streamShell } from "./shell.js";
export type { StreamShellOptions } from "./shell.js";
export { liveRegion, regionId, createSignalHub } from "./live.js";
export type { LiveRegion, SignalHub } from "./live.js";
export { defineAction, createActionRouter, parseMessage } from "./actions.js";
export type {
  ActionInput,
  ParsedMessage,
  ActionContext,
  ActionHandler,
  ActionDef,
  ActionRouterOptions,
} from "./actions.js";
