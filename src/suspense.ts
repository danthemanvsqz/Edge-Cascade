/**
 * Suspense / ErrorBoundary markers.
 *
 * These are identity markers, not real components — the renderer special-cases
 * them by function reference. Authoring them as functions keeps the JSX DX
 * uniform (`<Suspense fallback={…}>…</Suspense>`). Calling one directly is a
 * usage error (they only mean something to the renderer).
 */
import type { RawChild } from "./vnode.js";

export interface SuspenseProps {
  /** Shown inline (with a stable id) until the async subtree resolves. */
  fallback?: RawChild;
  children?: unknown;
  key?: string;
}

export interface ErrorBoundaryProps {
  /** Shown if the wrapped async subtree rejects. May be a function of error. */
  fallback?: RawChild | ((error: unknown) => RawChild);
  children?: unknown;
  key?: string;
}

export function Suspense(_props: SuspenseProps): never {
  throw new Error(
    "vinyl: <Suspense> is a renderer marker — render the tree with renderToStream",
  );
}

export function ErrorBoundary(_props: ErrorBoundaryProps): never {
  throw new Error(
    "vinyl: <ErrorBoundary> is a renderer marker — render the tree with renderToStream",
  );
}
