import { describe, it, expect } from "vitest";
import { VINYL } from "../src/index.js";

describe("M0 skeleton", () => {
  it("package entry is importable", () => {
    expect(VINYL).toBe("vinyl");
  });
});
