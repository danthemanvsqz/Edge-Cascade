/**
 * OOB framing primitive — the contract surface for the WS transport.
 *
 * Derived from the htmx-ws spike (ARCHITECTURE.md §7):
 *   - every top-level element of a WS frame is treated as OOB;
 *   - htmx defaults `hx-swap-oob` to `"true"` when absent — the attribute is
 *     redundant-but-documentary;
 *   - the wrapper id selects the target; the wrapper element replaces itself
 *     (default swap is `outerHTML`), so the SAME `<vinyl-slot id>` must exist
 *     in the initial shell.
 *
 * `oob()` is the entire framing layer. To send multiple OOB elements in a
 * single WS message, concatenate the strings — htmx applies them in source
 * order with one settle pass per frame.
 */

export const BOUNDARY_TAG = "vinyl-slot";

export function oobOpen(id: string): string {
  return `<${BOUNDARY_TAG} id="${id}" hx-swap-oob="true">`;
}

export function inlineOpen(id: string): string {
  return `<${BOUNDARY_TAG} id="${id}">`;
}

export function boundaryClose(): string {
  return `</${BOUNDARY_TAG}>`;
}

export function oob(id: string, html: string): string {
  return `${oobOpen(id)}${html}${boundaryClose()}`;
}
