import { defineConfig } from "tsup";

// M0: single entry. M1 adds ./jsx-runtime + ./jsx-dev-runtime entries.
export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  target: "node20",
  dts: true,
  sourcemap: true,
  clean: true,
  treeshake: true,
});
