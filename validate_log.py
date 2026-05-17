"""Validate the code the cascade produced (captured in runs/cascade.log).

Pipeline:  log  ->  (query, answer)  ->  repaired code block  ->  DSL checks.

The behavioural checks live in a tiny DSL (checks.dsl), not in this file, so
adding a new validation never means editing Python. See checks.dsl for the
2-construct grammar.

This deliberately execs model output and eval's DSL expressions, so it is an
OFFLINE dev tool only -- never wire it into the cascade hot path.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import random
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from cascade.feedback import CheckFailure, build_repair_prompt
from cascade.logfmt import parse_stream

ROOT = Path(__file__).parent
LOG = ROOT / "runs" / "cascade.log"
REC = ROOT / "runs" / "cascade.rec"
DSL = ROOT / "checks.dsl"
_TS = re.compile(r"^\d\d:\d\d:\d\d ")


# ---- log -> (query, answer) records -----------------------------------------

def parse_records(text: str) -> list[tuple[str, str]]:
    records, query, collecting, buf = [], None, False, []
    for ln in text.splitlines():
        q = re.match(r"^\d\d:\d\d:\d\d ---- QUERY: (.*)", ln)
        if q:
            if collecting:
                records.append((query or "?", "\n".join(buf)))
                collecting = False
            query = q.group(1)
            continue
        if re.match(r"^\d\d:\d\d:\d\d .*ANSWER:$", ln):
            collecting, buf = True, []
            continue
        if collecting:
            if _TS.match(ln):
                records.append((query or "?", "\n".join(buf)))
                collecting = False
            else:
                buf.append(ln)
    if collecting:
        records.append((query or "?", "\n".join(buf)))
    return records


def load_records() -> list[tuple[str, str]]:
    """(query, answer) records, preferring the deterministic .rec stream.

    cascade.rec is length-framed (cascade/logfmt.py) so it parses
    unambiguously even when an answer contains fake timestamps or the record
    sentinels. Falls back to regex-scraping the legacy human .log only when
    no structured stream exists (old runs / hand-written logs)."""
    if REC.exists() and REC.stat().st_size:
        return [
            (r.get("query", "?"), r.get("answer", ""))
            for r in parse_stream(REC.read_text(encoding="utf-8"))
        ]
    return parse_records(LOG.read_text(encoding="utf-8"))


# ---- answer -> compilable code (repairs truncated fences) -------------------

def extract_code(answer: str) -> str | None:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", answer, re.DOTALL)
    if m:
        code = m.group(1)
    else:
        m = re.search(r"```(?:python|py)?\s*\n(.*)", answer, re.DOTALL)
        if not m:
            return None
        code = m.group(1)
    work = code.rstrip()
    while work:
        try:
            compile(work, "<log>", "exec")
            return work
        except SyntaxError:
            work = work[: work.rfind("\n")] if "\n" in work else ""
    return None


# ---- the tiny DSL -----------------------------------------------------------

def parse_dsl(text: str) -> list[tuple[str, list[str]]]:
    """Return [(symbol, [(assert_expr, requirement), ...]), ...]."""
    blocks: list[tuple[str, list[tuple[str, str]]]] = []
    cur: list[tuple[str, str]] | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        if head == "when":
            cur = []
            blocks.append((rest.strip(), cur))
        elif head == "assert":
            if cur is None:
                raise SyntaxError(f"checks.dsl:{n}: 'assert' before any 'when'")
            expr, _, desc = rest.partition(" :: ")  # optional plain-lang req
            cur.append((expr.strip(), desc.strip()))
        else:
            raise SyntaxError(f"checks.dsl:{n}: unknown keyword {head!r}")
    return blocks


# ---- helpers injected into every assert namespace ---------------------------

def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def sorts_like(fn):
    rng = random.Random(0)
    cases = [[], [1], [2, 1], [5, 3, 3, 1, 9, 2],
             [rng.randint(-50, 50) for _ in range(40)]]
    for c in cases:
        assert fn(list(c)) == sorted(c), f"wrong on {c}"
    return True


def is_avl(cls):
    tree, root = cls(), None
    keys = [10, 20, 30, 40, 50, 25, 5, 15, 35, 45]
    for k in keys:
        root = tree.insert(root, k)

    def h(n):
        return 0 if n is None else 1 + max(h(n.left), h(n.right))

    def inorder(n, acc):
        if n:
            inorder(n.left, acc)
            acc.append(n.key)
            inorder(n.right, acc)

    def balanced(n):
        if n is None:
            return
        assert -1 <= h(n.left) - h(n.right) <= 1, f"unbalanced at {n.key}"
        balanced(n.left)
        balanced(n.right)

    acc: list[int] = []
    inorder(root, acc)
    assert acc == sorted(keys), f"not a BST: {acc}"
    balanced(root)
    for k in (10, 30, 50):
        root = tree.delete(root, k)
    acc = []
    inorder(root, acc)
    assert acc == sorted(set(keys) - {10, 30, 50}), f"bad after delete: {acc}"
    balanced(root)
    return True


def drone_ok(fn):
    g = {"A": {"B": 4, "C": 2}, "B": {"C": 1, "D": 5},
         "C": {"D": 8, "E": 10}, "D": {"E": 2}}
    r = fn(g, "A")
    if isinstance(r, dict):
        cost = r.get("E")
    elif isinstance(r, (int, float)):
        cost = r
    elif isinstance(r, (tuple, list)) and r and isinstance(r[0], (int, float)):
        cost = r[0]
    else:
        cost = None
    assert cost == 11, f"A->E min battery must be 11, got {cost!r}"
    return True


HELPERS = {"approx": approx, "sorts_like": sorts_like, "is_avl": is_avl,
           "drone_ok": drone_ok}


# ---- run --------------------------------------------------------------------

@dataclass
class Check:
    sym: str
    expr: str
    ok: bool
    observed: str
    requirement: str = ""


def _defs_only(code: str) -> str | None:
    """Blank out top-level demo/script lines, keeping defs/classes/imports.

    Blanking (not ast.unparse) preserves original line numbers, so the
    located error fed to repair matches the code the model is shown.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    lines = code.splitlines()
    keep = [False] * (len(lines) + 2)
    found = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Import, ast.ImportFrom)):
            found = True
            for i in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                keep[i] = True
    if not found:
        return None
    return "\n".join(
        lines[i - 1] if keep[i] else "" for i in range(1, len(lines) + 1)
    )


def _fmt_exc(e: BaseException) -> str:
    """Exception + the deepest in-code frame, so repair knows *where*."""
    frames = traceback.extract_tb(e.__traceback__)
    for fr in reversed(frames):
        if fr.name != "<module>":
            return f"{type(e).__name__}: {e} (in {fr.name}(), line {fr.lineno})"
    if frames:
        return f"{type(e).__name__}: {e} (line {frames[-1].lineno})"
    return f"{type(e).__name__}: {e}"


def _safe_exec(code: str, ns: dict) -> str | None:
    """Exec code; return None on success or a located error string."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            # Analysed & accepted (bandit B102): offline dev validator only,
            # never in the cascade request path; runs developer-reviewed
            # model output to check correctness. Replacement is impossible --
            # the tool's whole job is to execute the candidate code.
            exec(code, ns)  # nosec B102
        return None
    except Exception as e:
        return _fmt_exc(e)


def run(record_code: str, blocks) -> list[Check]:
    ns: dict = dict(HELPERS)
    err = _safe_exec(record_code, ns)
    if err is not None:
        # Module-level demo crashed -> retry with defs/imports only so the
        # function-level DSL checks still run and yield task-level feedback.
        stripped = _defs_only(record_code)
        if stripped is None:
            return [Check("<exec>", "import/define", False, err)]
        ns = dict(HELPERS)
        err2 = _safe_exec(stripped, ns)
        if err2 is not None:
            return [Check("<exec>", "import/define", False, err2)]
    checks: list[Check] = []
    for sym, asserts in blocks:
        if sym not in ns:
            continue
        for expr, req in asserts:
            try:
                # Analysed & accepted (bandit B307): evaluates assertion
                # expressions from checks.dsl -- a trusted, repo-owned,
                # developer-authored file, never user/network input. Offline
                # tool only. ast.literal_eval can't evaluate predicates.
                val = eval(expr, ns)  # nosec B307
                checks.append(Check(
                    sym, expr, bool(val),
                    "ok" if val else f"evaluated to {val!r}", req,
                ))
            except Exception as e:
                checks.append(Check(sym, expr, False, _fmt_exc(e), req))
    return checks


def _make_repairer(tier: str):
    """Return (label, prompt->text) for the chosen repair tier, or (None, why)."""
    if tier == "npu":
        from cascade.config import CONFIG
        from cascade.npu_worker import NPUWorker

        w = NPUWorker()
        cap = CONFIG.npu_repair_max_tokens
        return (f"NPU ({w.device})",
                lambda p: w.draft(p, max_new_tokens=cap).text)
    from cascade.gpu_worker import make_gpu_worker

    w = make_gpu_worker()
    if not w.available():
        return None, "GPU tier unavailable (Ollama not reachable)"
    return "NVIDIA GPU", lambda p: w.generate(p).text


def repair(task: str, code: str, fails: list[Check], blocks,
           tier: str = "gpu", rounds: int = 2) -> tuple[bool, str]:
    """Feed failures back to a model (gpu or npu) via the repair protocol."""
    label, gen = _make_repairer(tier)
    if label is None:
        return False, gen  # gen holds the reason string
    for r in range(1, rounds + 1):
        prompt = build_repair_prompt(
            task, code,
            [CheckFailure(c.expr, c.observed, c.requirement) for c in fails],
        )
        print(f"      -> repair protocol -> {label} (round {r})")
        fixed = extract_code(gen(prompt))
        if fixed is None:
            return False, f"round {r}: {label} returned no usable code block"
        still = [c for c in run(fixed, blocks) if not c.ok]
        if not still:
            return True, f"repaired by {label} in round {r}"
        code, fails = fixed, still
    return False, f"{label} still failing after {rounds} round(s)"


def main() -> None:
    ap = argparse.ArgumentParser(description="DSL-validate logged cascade code")
    ap.add_argument("--repair", action="store_true",
                    help="feed failures back to a model to fix")
    ap.add_argument("--repair-tier", choices=("gpu", "npu"), default="gpu",
                    help="which tier repairs (default gpu)")
    ap.add_argument("--selftest", action="store_true",
                    help="inject a known-buggy record to demo the repair loop")
    args = ap.parse_args()

    blocks = parse_dsl(DSL.read_text(encoding="utf-8"))
    if args.selftest:
        recs = [("write a python function to add two numbers",
                 "```python\ndef add_numbers(a, b):\n    return a - b\n```")]
    else:
        recs = load_records()
    print(f"{DSL.name}: {len(blocks)} block(s) | "
          f"{len(recs)} answer(s) | repair={args.repair}\n")

    unresolved = 0
    for i, (q, ans) in enumerate(recs, 1):
        print(f"[{i}] QUERY: {q}")
        code = extract_code(ans)
        if code is None:
            print("    no usable code block\n")
            continue
        checks = run(code, blocks)
        for c in checks:
            tag = "PASS" if c.ok else "FAIL"
            print(f"    {tag} [{c.sym}] {c.expr}"
                  + ("" if c.ok else f"  ({c.observed})"))
        fails = [c for c in checks if not c.ok]
        if fails and args.repair:
            ok, msg = repair(q, code, fails, blocks, tier=args.repair_tier)
            print(f"    REPAIR: {msg}")
            unresolved += not ok
        else:
            unresolved += bool(fails)
        print()
    sys.exit(1 if unresolved else 0)


if __name__ == "__main__":
    main()
