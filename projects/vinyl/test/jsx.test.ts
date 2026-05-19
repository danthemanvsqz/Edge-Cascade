import { describe, it, expect } from "vitest";
import { jsx, jsxs, Fragment } from "../src/jsx-runtime.js";
import { jsxDEV } from "../src/jsx-dev-runtime.js";
import { isVNode } from "../src/vnode.js";

describe("automatic JSX runtime", () => {
  it("jsx produces the same vnode shape as createVNode", () => {
    expect(jsx("div", { id: "x", children: "hi" }, "k")).toEqual({
      type: "div",
      props: { id: "x" },
      children: ["hi"],
      key: "k",
    });
  });

  it("jsxs (static children array) flattens identically", () => {
    const tree = jsxs("ul", {
      children: [jsx("li", { children: "a" }), jsx("li", { children: "b" })],
    });
    expect(tree.children).toHaveLength(2);
    expect(tree.children.every(isVNode)).toBe(true);
  });

  it("jsx is jsxs (same impl)", () => {
    expect(jsxs).toBe(jsx);
  });

  it("missing key → null", () => {
    expect(jsx("span", {}).key).toBeNull();
  });

  it("Fragment passes through as the vnode type", () => {
    expect(jsx(Fragment, { children: ["a", "b"] }).type).toBe(Fragment);
  });

  it("hx-*/ws-* and class attribute names are preserved verbatim", () => {
    const v = jsx("button", {
      class: "btn",
      "hx-post": "/save",
      "ws-send": true,
    });
    expect(v.props).toEqual({
      class: "btn",
      "hx-post": "/save",
      "ws-send": true,
    });
  });
});

describe("dev JSX runtime", () => {
  it("jsxDEV ignores the dev-only trailing args and yields the same vnode", () => {
    expect(
      jsxDEV("p", { children: "x" }, "k", true, { fileName: "a.tsx" }, undefined),
    ).toEqual({ type: "p", props: {}, children: ["x"], key: "k" });
  });
});
