import { describe, it, expect } from "vitest";
import {
  Fragment,
  h,
  createVNode,
  isVNode,
  flattenChildren,
  normalizeProps,
} from "../src/vnode.js";

describe("flattenChildren", () => {
  it("flattens nested arrays and drops null/undefined/booleans, keeps 0 and ''", () => {
    expect(
      flattenChildren([0, "", false, null, "a", [["b"], undefined], true]),
    ).toEqual([0, "", "a", "b"]);
  });

  it("wraps a single non-array child", () => {
    expect(flattenChildren("solo")).toEqual(["solo"]);
    expect(flattenChildren(undefined)).toEqual([]);
  });

  it("keeps VNode objects unchanged", () => {
    const v = createVNode("span", null);
    expect(flattenChildren([v, null, [v]])).toEqual([v, v]);
  });
});

describe("normalizeProps", () => {
  it("strips reserved children/key and copies the rest", () => {
    const src = { id: "x", children: "c", key: "k", "hx-get": "/u" };
    expect(normalizeProps(src)).toEqual({ id: "x", "hx-get": "/u" });
  });

  it("null/undefined → {}", () => {
    expect(normalizeProps(null)).toEqual({});
    expect(normalizeProps(undefined)).toEqual({});
  });

  it("does not mutate the caller's object", () => {
    const src = { id: "x", children: "c" };
    normalizeProps(src);
    expect(src).toEqual({ id: "x", children: "c" });
  });
});

describe("createVNode", () => {
  it("pulls children from props, strips children/key, key arg wins", () => {
    expect(
      createVNode("div", { id: "x", children: "hi", key: "ignored" }, "k"),
    ).toEqual({ type: "div", props: { id: "x" }, children: ["hi"], key: "k" });
  });

  it("null props → empty props, no children, null key", () => {
    expect(createVNode("div", null)).toEqual({
      type: "div",
      props: {},
      children: [],
      key: null,
    });
  });

  it("does not mutate the caller's props", () => {
    const props = { id: "x", children: ["a", "b"] };
    createVNode("div", props);
    expect(props).toEqual({ id: "x", children: ["a", "b"] });
  });
});

describe("h (hyperscript)", () => {
  it("variadic children become flattened child vnodes", () => {
    const tree = h("ul", null, h("li", null, "a"), h("li", null, "b"));
    expect(tree.type).toBe("ul");
    expect(tree.children).toHaveLength(2);
    expect((tree.children[0] as { type: unknown }).type).toBe("li");
  });

  it("falls back to props.children when no variadic args", () => {
    expect(h("p", { children: "fromprops" }).children).toEqual(["fromprops"]);
  });

  it("variadic children override props.children", () => {
    expect(h("p", { children: "ignored" }, "win").children).toEqual(["win"]);
  });

  it("reads key from props in both branches", () => {
    expect(h("x", { key: "k" }).key).toBe("k");
    expect(h("x", { key: "k" }, h("y", null)).key).toBe("k");
    expect(h("x", { key: 42 }).key).toBeNull(); // non-string key ignored
  });

  it("Fragment is usable as a type", () => {
    const f = h(Fragment, null, "a", "b");
    expect(f.type).toBe(Fragment);
    expect(f.children).toEqual(["a", "b"]);
  });
});

describe("isVNode", () => {
  it("true for created vnodes, false for primitives/plain objects", () => {
    expect(isVNode(createVNode("div", null))).toBe(true);
    expect(isVNode("a")).toBe(false);
    expect(isVNode(null)).toBe(false);
    expect(isVNode({ type: "div" })).toBe(false);
  });
});
