/**
 * Deterministic, collision-free ids for Suspense/ErrorBoundary boundaries.
 *
 * The id is path-based: a segment per tree depth. By default a segment is
 * the child's index within its parent; a user-supplied `key` prop overrides
 * that segment (so ids stay stable across re-renders even when sibling order
 * shifts, as long as keys are stable). The same scheme is reused by live
 * regions in M5.
 */

/** Normalize a user key into a DOM-id-safe segment. */
export function safeSeg(s: string): string {
  const out = s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return out === "" ? "_" : out;
}

/** Extend a dotted boundary path with one more segment. */
export function childPath(parentPath: string, segment: string | number): string {
  return parentPath === "" ? String(segment) : `${parentPath}.${String(segment)}`;
}

/** Stable DOM id for a boundary at (parentPath, index | key). */
export function boundaryId(
  parentPath: string,
  indexInParent: number,
  key: string | null,
): string {
  const seg = key !== null ? safeSeg(key) : String(indexInParent);
  return "vinyl-s-" + childPath(parentPath, seg).replace(/\./g, "-");
}
