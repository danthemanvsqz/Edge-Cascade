/**
 * M4 — OOB framing primitive. The shape MUST match what M3 already streams,
 * because the htmx-ws contract (ARCHITECTURE.md §7) says the same
 * `<vinyl-slot id>` wrapper must exist in the shell and be `outerHTML`-
 * replaced by the pushed frame.
 */
import { describe, it, expect } from "vitest";
import { h } from "../src/vnode.js";
import type { VNodeChild } from "../src/vnode.js";
import { renderToStream } from "../src/render.js";
import { Suspense } from "../src/suspense.js";
import { oob, BOUNDARY_TAG } from "../src/oob.js";

async function html(node: unknown): Promise<string> {
  let out = "";
  for await (const c of renderToStream(node)) out += c;
  return out;
}

describe("M4 — oob() framing", () => {
  it("emits the exact <vinyl-slot id hx-swap-oob='true'> wrapper", () => {
    expect(oob("vinyl-s-0", "<p>done</p>")).toBe(
      `<${BOUNDARY_TAG} id="vinyl-s-0" hx-swap-oob="true"><p>done</p></${BOUNDARY_TAG}>`,
    );
  });

  it("matches byte-for-byte what M3 streams for the resolved boundary", async () => {
    const Slow = async (): Promise<VNodeChild> => {
      await Promise.resolve();
      return h("p", null, "done");
    };
    const tree = h(
      Suspense,
      { fallback: h("span", null, "loading") },
      h(Slow, null),
    );

    const s = await html(tree);
    expect(s).toContain(oob("vinyl-s-0", "<p>done</p>"));
  });

  it("multiple OOB elements concatenate into a single frame body", () => {
    const frame = [
      oob("a", "<i>1</i>"),
      oob("b", "<i>2</i>"),
      oob("c", "<i>3</i>"),
    ].join("");
    expect(frame).toBe(
      `<${BOUNDARY_TAG} id="a" hx-swap-oob="true"><i>1</i></${BOUNDARY_TAG}>` +
        `<${BOUNDARY_TAG} id="b" hx-swap-oob="true"><i>2</i></${BOUNDARY_TAG}>` +
        `<${BOUNDARY_TAG} id="c" hx-swap-oob="true"><i>3</i></${BOUNDARY_TAG}>`,
    );
  });
});
