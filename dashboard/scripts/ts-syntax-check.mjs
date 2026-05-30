// Syntax-only TypeScript gate (BACKLOG #7). Reads a TS snippet on stdin,
// single-file-transpiles it (NO type-checking, NO import resolution), and
// reports the first SYNTACTIC error as JSON: {passed:true} or
// {passed:false, reason}. Parity with cascade.verifier's Python AST check --
// it transpiles, never executes, the candidate. `typescript` resolves from
// dashboard/node_modules via this file's location.
import ts from "typescript";

let src = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  src += chunk;
});
process.stdin.on("end", () => {
  const out = ts.transpileModule(src, {
    reportDiagnostics: true,
    compilerOptions: {},
  });
  const errors = (out.diagnostics ?? []).filter(
    (d) => d.category === ts.DiagnosticCategory.Error,
  );
  const result =
    errors.length === 0
      ? { passed: true }
      : {
          passed: false,
          reason: ts.flattenDiagnosticMessageText(errors[0].messageText, " "),
        };
  process.stdout.write(JSON.stringify(result));
});
