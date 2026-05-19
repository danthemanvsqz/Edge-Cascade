import { describe, it, expect } from "vitest";
import { safeSeg, childPath, boundaryId } from "../src/ids.js";

describe("safeSeg", () => {
  it("normalizes to a DOM-id-safe segment", () => {
    expect(safeSeg("Hello World!")).toBe("hello-world");
    expect(safeSeg("a__b  c")).toBe("a-b-c");
    expect(safeSeg("!!!")).toBe("_");
    expect(safeSeg("")).toBe("_");
    expect(safeSeg("OK")).toBe("ok");
  });
});

describe("childPath", () => {
  it("roots the first segment, then dots", () => {
    expect(childPath("", 0)).toBe("0");
    expect(childPath("0", 2)).toBe("0.2");
    expect(childPath("0.2", "todo")).toBe("0.2.todo");
  });
});

describe("boundaryId", () => {
  it("index-based by default, key overrides the segment", () => {
    expect(boundaryId("", 0, null)).toBe("vinyl-s-0");
    expect(boundaryId("0.1", 3, null)).toBe("vinyl-s-0-1-3");
    expect(boundaryId("0", 5, "User Card")).toBe("vinyl-s-0-user-card");
  });

  it("same inputs → same id (stable across re-renders)", () => {
    expect(boundaryId("0.1", 2, "k")).toBe(boundaryId("0.1", 2, "k"));
  });

  it("distinct positions → distinct ids (collision-free)", () => {
    const a = boundaryId("0", 1, null);
    const b = boundaryId("0", 2, null);
    const c = boundaryId("1", 1, null);
    expect(new Set([a, b, c]).size).toBe(3);
  });
});
