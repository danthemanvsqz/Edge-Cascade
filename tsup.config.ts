import { defineConfig } from "tsup";

// M1: index + the two JSX runtime entries (matched in package.json exports).
export default defineConfig({
  entry: [
    "src/index.ts",
    "src/jsx-runtime.ts",
    "src/jsx-dev-runtime.ts",
  ],
  format: ["esm"],
  target: "node20",
  dts: true,
  sourcemap: true,
  clean: true,
  treeshake: true,
});
