/**
 * M3 — the novel core. Async components + <Suspense>/<ErrorBoundary>:
 * inline fallback behind a stable id, out-of-order/progressive flush via
 * out-of-band frames, slow-never-blocks-fast, structural error recovery, and
 * a deterministic, collision-free, user-overridable id scheme. This is the
 * milestone's heaviest coverage by design (PLAN.md M3).
 */
import { describe, it, expect } from "vitest";
import { h, Fragment } from "../src/vnode.js";
import { renderToString, renderToStream } from "../src/render.js";
import { Suspense, ErrorBoundary } from "../src/suspense.js";
import { boundaryId } from "../src/ids.js";

const tick = (ms = 0): Promise<void> =>
  new Promise((r) => setTimeout(r, ms));

/** Drain the stream into its ordered chunk list. */
async function chunks(node: unknown): Promise<string[]> {
  const out: string[] = [];
  for await (const c of renderToStream(node)) out.push(c);
  return out;
}

async function html(node: unknown): Promise<string> {
  return (await chunks(node)).join("");
}

/** Every `<vinyl-slot id="…">` id in render order (inline + oob frames). */
function slotIds(s: string): string[] {
  return [...s.matchAll(/<vinyl-slot id="([^"]+)"/g)].map((m) => m[1] as string);
}

describe("M3 — async component under <Suspense>", () => {
  it("emits inline fallback behind a stable id, then an oob frame on resolve", async () => {
    const Slow = async () => {
      await tick(5);
      return h("p", null, "done");
    };
    const tree = h(
      Suspense,
      { fallback: h("span", null, "loading") },
      h(Slow, null),
    );

    expect(await html(tree)).toBe(
      '<vinyl-slot id="vinyl-s-0"><span>loading</span></vinyl-slot>' +
        '<vinyl-slot id="vinyl-s-0" hx-swap-oob="true"><p>done</p></vinyl-slot>',
    );
  });

  it("a fully synchronous <Suspense> is transparent — no wrapper, no fallback", async () => {
    const tree = h(
      Suspense,
      { fallback: h("span", null, "fb") },
      h("b", null, "hi"),
    );
    expect(await html(tree)).toBe("<b>hi</b>");
    // String form agrees (no async ⇒ no streaming concept involved).
    expect(renderToString(tree)).toBe("<b>hi</b>");
  });
});

describe("M3 — out-of-order: slow never blocks fast", () => {
  it("streams the whole shell first, then frames in resolve order", async () => {
    const Fast = async () => {
      await tick(5);
      return h("i", null, "fast");
    };
    const Slow = async () => {
      await tick(40);
      return h("i", null, "slow");
    };
    const tree = h(
      Fragment,
      null,
      h(Suspense, { fallback: h("span", null, "fb-fast") }, h(Fast, null)),
      h("hr", null),
      h(Suspense, { fallback: h("span", null, "fb-slow") }, h(Slow, null)),
    );

    const parts = await chunks(tree);
    const s = parts.join("");

    const shell =
      '<vinyl-slot id="vinyl-s-0-0"><span>fb-fast</span></vinyl-slot>' +
      "<hr>" +
      '<vinyl-slot id="vinyl-s-0-2"><span>fb-slow</span></vinyl-slot>';
    // The entire synchronous shell flushes before any deferred frame.
    expect(s.startsWith(shell)).toBe(true);

    const fastFrame =
      '<vinyl-slot id="vinyl-s-0-0" hx-swap-oob="true"><i>fast</i></vinyl-slot>';
    const slowFrame =
      '<vinyl-slot id="vinyl-s-0-2" hx-swap-oob="true"><i>slow</i></vinyl-slot>';
    expect(s).toContain(fastFrame);
    expect(s).toContain(slowFrame);
    // Fast resolves first ⇒ its frame precedes the slow one in the stream,
    // even though the slow boundary was reached first in source order.
    expect(s.indexOf(fastFrame)).toBeLessThan(s.indexOf(slowFrame));
    // Each frame is its own discrete chunk appended after the shell.
    expect(parts).toContain(fastFrame);
    expect(parts).toContain(slowFrame);
  });
});

describe("M3 — <ErrorBoundary> is structural", () => {
  it("a rejecting async subtree renders the fallback and never kills the stream", async () => {
    const Boom = async () => {
      await tick(5);
      throw new Error("kaboom");
    };
    const tree = h(
      Suspense,
      { fallback: h("span", null, "fb") },
      h(
        ErrorBoundary,
        { fallback: (e: unknown) => h("p", null, `err:${(e as Error).message}`) },
        h(Boom, null),
      ),
    );

    // The for-await loop completing (no throw) is itself the proof that a
    // rejected subtree does not tear down the socket.
    await expect(html(tree)).resolves.toBe(
      '<vinyl-slot id="vinyl-s-0"><span>fb</span></vinyl-slot>' +
        '<vinyl-slot id="vinyl-s-0" hx-swap-oob="true"><p>err:kaboom</p></vinyl-slot>',
    );
  });
});

describe("M3 — nested / progressive <Suspense>", () => {
  it("an inner boundary registers its own frame; outer flushes before inner", async () => {
    const Inner = async () => {
      await tick(10);
      return h("i", null, "in");
    };
    const Outer = async () => {
      await tick(5);
      return h(
        Suspense,
        { fallback: h("span", null, "inner-fb") },
        h(Inner, null),
      );
    };
    const tree = h(
      Suspense,
      { fallback: h("span", null, "outer-fb") },
      h(Outer, null),
    );

    const s = await html(tree);
    expect(s).toBe(
      '<vinyl-slot id="vinyl-s-0"><span>outer-fb</span></vinyl-slot>' +
        '<vinyl-slot id="vinyl-s-0" hx-swap-oob="true">' +
        '<vinyl-slot id="vinyl-s-0-0-0"><span>inner-fb</span></vinyl-slot>' +
        "</vinyl-slot>" +
        '<vinyl-slot id="vinyl-s-0-0-0" hx-swap-oob="true"><i>in</i></vinyl-slot>',
    );
  });
});

describe("M3 — renderToString stays synchronous", () => {
  it("directs async-without-Suspense and async-inside-Suspense to renderToStream", () => {
    const Async = () => Promise.resolve(h("div", null));

    expect(() => renderToString(h(Async, null))).toThrow(
      /async components require <Suspense>/,
    );
    expect(() =>
      renderToString(
        h(Suspense, { fallback: h("span", null, "x") }, h(Async, null)),
      ),
    ).toThrow(/inside <Suspense> requires renderToStream/);
  });
});

describe("M3 — id scheme: deterministic, collision-free, key-overridable", () => {
  it("rendered ids match boundaryId(path, index, key)", () => {
    expect(boundaryId("", 0, null)).toBe("vinyl-s-0");
    expect(boundaryId("0", 1, "kb")).toBe("vinyl-s-0-kb");
    expect(boundaryId("0.0", 2, null)).toBe("vinyl-s-0-0-2");
  });

  it("a user `key` overrides the index segment of the boundary id", async () => {
    const Slow = async () => {
      await tick(5);
      return h("p", null, "x");
    };
    const tree = h(
      Suspense,
      { fallback: h("span", null, "fb"), key: "User Card" },
      h(Slow, null),
    );

    const s = await html(tree);
    expect(s).toContain('<vinyl-slot id="vinyl-s-user-card">');
    expect(s).toContain(
      '<vinyl-slot id="vinyl-s-user-card" hx-swap-oob="true"><p>x</p></vinyl-slot>',
    );
    // The key must actually win — not silently fall back to the index id.
    expect(s).not.toContain('id="vinyl-s-0"');
  });

  it("sibling boundary ids are distinct and stable across re-renders", async () => {
    const A = async () => {
      await tick(3);
      return h("i", null, "a");
    };
    const B = async () => {
      await tick(3);
      return h("i", null, "b");
    };
    const tree = h(
      Fragment,
      null,
      h(Suspense, { fallback: h("span", null, "fa") }, h(A, null)),
      h(Suspense, { fallback: h("span", null, "fb"), key: "kb" }, h(B, null)),
    );

    const r1 = await html(tree);
    const r2 = await html(tree);
    expect(r1).toBe(r2); // stable across renders

    const ids = slotIds(r1);
    // Two boundaries, each id used twice (inline fallback + oob frame).
    expect(ids).toHaveLength(4);
    expect(new Set(ids)).toEqual(new Set(["vinyl-s-0-0", "vinyl-s-0-kb"]));
  });
});
