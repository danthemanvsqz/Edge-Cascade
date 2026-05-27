"""PD-1 v2 warn-prompt empirical validation, EXPERIMENT v2.

Follow-up to the first warn-prompt experiment (#68) which collapsed because
the subject pool fell at the GPU repair ceiling (100% pass on both arms).
The v2 pivots the task battery to PARSER / INTERPRETER / STATE-MACHINE tasks
-- the v2 doc's identified Tier-2 frontier where the 14B model has imperfect
first-shot pass, so warn-prompt has headroom to help.

Hardening over v1:

  - DSL SELF-TESTS at module load. Each task carries a `good_impl` and
    `broken_impl` string; `_self_test_dsls()` runs them through the gate
    and asserts (good passes 100%, broken fails). A DSL bug raises at
    import time, not at Phase 2 when 60 trials have already been spent.

  - Phase 1 NOW DISTINGUISHES "DSL-parse-crashed" from "gate-FAIL with
    failures". A sandbox crash (sandbox-must-run failure) means the DSL is
    broken and the draft is unfilterable; we DON'T accept it as a subject.
    Only drafts where the gate ran cleanly AND reported assertion failures
    count.

Phases (idempotent on persisted artifacts -- safe to re-enter):

  Phase 1  Subject generation. NPU drafts the battery; filter to
           (gate-FAIL with assertion failures) AND (text_reasons non-empty).
           Writes runs/warn-prompt-validation-v2/subjects.jsonl.

  Phase 2  A/B sweep. Per subject, 30 control + 30 treatment GPU repair
           trials interleaved. Per-subject JSON checkpoint after each trial.
           keep_awake holds a Windows wake-lock.
           Records to runs/experiment-warn-prompt-validation-v2.rec.

  Phase 3  Bayesian-MC analysis. Beta(1+pass, 1+fail) per arm, 10k draws,
           P(trt > ctrl) per subject and pooled. Writes summary.json.

Reproduce:
    git switch experiment/warn-prompt-validation-v2-2026-05-27
    uv run python scripts/warn_prompt_validation_v2.py
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import random
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402
from cascade.degeneration import (  # noqa: E402
    Thresholds,
    check_degeneration,
)
from cascade.feedback import CheckFailure, build_repair_prompt  # noqa: E402
from cascade.gpu_worker import _generate as gpu_generate  # noqa: E402
from cascade.npu_worker import make_npu_worker  # noqa: E402
from mcp_servers._rec import make_experiment_recorder  # noqa: E402

# -- Experiment config -----------------------------------------------------

TOPIC = "warn-prompt-validation-v2"
OUT_DIR = ROOT / "runs" / TOPIC
SUBJECTS_PATH = OUT_DIR / "subjects.jsonl"
SUMMARY_PATH = OUT_DIR / "summary.json"

# Trial budget per subject per arm. Skill: "30+ trials/cell".
N_TRIALS_PER_ARM = 30

# Phase 1: drafts per task. Higher than v1 (8 -> 12) because the parser
# battery may have lower per-task yield in the failure-rich band.
NPU_DRAFTS_PER_TASK = 12

# Phase 3 Monte Carlo draws for P(trt > ctrl). Seeded for repro.
MC_DRAWS = 10_000
MC_SEED = 27

# Decision thresholds (from the v1 plan, unchanged).
SHIP_P = 0.90
SHIP_EFFECT_PP = 10
REVERT_P = 0.10

REPAIR_MAX_TOKENS = 1024  # parser tasks can be longer than v1's algo tasks

# Thresholds the warn-prompt signal was calibrated against (v2 from #66).
THRESHOLDS_PATH = ROOT / "cascade" / "degeneration_thresholds.json"

# -- Task battery: parsers / interpreters / state machines ------------------
#
# Each task is (slug, prompt, dsl, good_impl, broken_impl):
#  - slug         : exact function/class name the gate's `when` will match
#  - prompt       : NPU draft prompt
#  - dsl          : `when <slug>` + `assert <expr>` lines (NO setup lines --
#                   they'd violate validate_log.parse_dsl). Use immediately-
#                   invoked-lambda for any multi-step setup.
#  - good_impl    : a self-contained correct implementation (fenced) that
#                   the DSL must pass at module load.
#  - broken_impl  : a deliberately-broken implementation (fenced) that the
#                   DSL must fail at module load.
#
# Aim: failure-rich band for the qwen2.5-coder:14b GPU repair model.
# Tier-1 (1.5B) routinely loops on nested-state parser logic; Tier-2 (14B)
# usually solves these but with imperfect first-shot accuracy on edge cases.

TASKS = [
    (
        "decode_string",
        "Write a Python function `decode_string(s)` that decodes strings of "
        "the form `3[a]2[bc]` -> `aaabcbc`. Numbers indicate repetition count "
        "of the bracketed substring; nesting like `3[a2[c]]` -> `accaccacc` "
        "is required. Use a stack. One fenced ```python``` block.",
        "when decode_string\n"
        "  assert decode_string('3[a]2[bc]') == 'aaabcbc'\n"
        "  assert decode_string('3[a2[c]]') == 'accaccacc'\n"
        "  assert decode_string('abc') == 'abc'\n"
        "  assert decode_string('2[abc]3[cd]ef') == 'abcabccdcdcdef'\n",
        "```python\n"
        "def decode_string(s):\n"
        "    stack = []; cur = ''; k = 0\n"
        "    for ch in s:\n"
        "        if ch.isdigit():\n"
        "            k = k * 10 + int(ch)\n"
        "        elif ch == '[':\n"
        "            stack.append((cur, k)); cur = ''; k = 0\n"
        "        elif ch == ']':\n"
        "            prev, n = stack.pop(); cur = prev + cur * n\n"
        "        else:\n"
        "            cur += ch\n"
        "    return cur\n"
        "```",
        "```python\n"
        "def decode_string(s):\n"
        "    return s  # broken: returns input unchanged\n"
        "```",
    ),
    (
        "simplify_path",
        "Write a Python function `simplify_path(path)` that simplifies a "
        "Unix-style absolute path. Handles `.` (current), `..` (parent), and "
        "multiple `/`. Returns the canonical path starting with `/`, no "
        "trailing slash unless root. One fenced ```python``` block.",
        "when simplify_path\n"
        "  assert simplify_path('/home/') == '/home'\n"
        "  assert simplify_path('/../') == '/'\n"
        "  assert simplify_path('/home//foo/') == '/home/foo'\n"
        "  assert simplify_path('/a/./b/../../c/') == '/c'\n",
        "```python\n"
        "def simplify_path(path):\n"
        "    parts = []\n"
        "    for tok in path.split('/'):\n"
        "        if tok in ('', '.'): continue\n"
        "        if tok == '..':\n"
        "            if parts: parts.pop()\n"
        "        else:\n"
        "            parts.append(tok)\n"
        "    return '/' + '/'.join(parts)\n"
        "```",
        "```python\n"
        "def simplify_path(path):\n"
        "    return path  # broken: no simplification\n"
        "```",
    ),
    (
        "calculator_basic",
        "Write a Python function `calculator_basic(expr)` that evaluates an "
        "arithmetic expression of non-negative integers with operators "
        "+, -, *, /  (integer division, truncating toward zero) WITHOUT "
        "parentheses, respecting operator precedence (*/ before +-). Returns "
        "an int. One fenced ```python``` block.",
        "when calculator_basic\n"
        "  assert calculator_basic('3+5*2') == 13\n"
        "  assert calculator_basic('14-3*2') == 8\n"
        "  assert calculator_basic('100') == 100\n"
        "  assert calculator_basic('14/3+2') == 6\n"
        "  assert calculator_basic('1+2+3') == 6\n",
        "```python\n"
        "def calculator_basic(expr):\n"
        "    import re\n"
        "    toks = re.findall(r'\\d+|[+\\-*/]', expr)\n"
        "    # first pass: */\n"
        "    i = 1\n"
        "    nums = [int(toks[0])]; ops = []\n"
        "    while i < len(toks):\n"
        "        op = toks[i]; n = int(toks[i+1])\n"
        "        if op in '*/':\n"
        "            if op == '*': nums[-1] *= n\n"
        "            else: nums[-1] = int(nums[-1] / n)\n"
        "        else:\n"
        "            ops.append(op); nums.append(n)\n"
        "        i += 2\n"
        "    r = nums[0]\n"
        "    for k, op in enumerate(ops, 1):\n"
        "        r = r + nums[k] if op == '+' else r - nums[k]\n"
        "    return r\n"
        "```",
        "```python\n"
        "def calculator_basic(expr):\n"
        "    return eval(expr)  # broken: ignores integer-div semantics\n"
        "```",
    ),
    (
        "validate_brackets",
        "Write a Python function `validate_brackets(s)` that returns True iff "
        "the string contains only `()[]{}` and they are properly matched and "
        "nested. Empty string returns True. One fenced ```python``` block.",
        "when validate_brackets\n"
        "  assert validate_brackets('()[]{}') is True\n"
        "  assert validate_brackets('([{}])') is True\n"
        "  assert validate_brackets('(]') is False\n"
        "  assert validate_brackets('') is True\n"
        "  assert validate_brackets('((') is False\n"
        "  assert validate_brackets('){') is False\n"
        # Mis-nested but balanced counts -- discriminates against a naive\n"
        # 'count_open == count_close' impl that ignores order and type.\n"
        "  assert validate_brackets('([)]') is False\n"
        "  assert validate_brackets('[(])') is False\n",
        "```python\n"
        "def validate_brackets(s):\n"
        "    stack = []\n"
        "    pairs = {')': '(', ']': '[', '}': '{'}\n"
        "    for ch in s:\n"
        "        if ch in '([{':\n"
        "            stack.append(ch)\n"
        "        elif ch in ')]}':\n"
        "            if not stack or stack[-1] != pairs[ch]: return False\n"
        "            stack.pop()\n"
        "    return not stack\n"
        "```",
        "```python\n"
        "def validate_brackets(s):\n"
        "    return s.count('(') == s.count(')')  # broken: ignores nesting + types\n"
        "```",
    ),
    (
        "evaluate_postfix",
        "Write a Python function `evaluate_postfix(tokens)` that evaluates a "
        "list of postfix (RPN) tokens. Tokens are integer strings or one of "
        "+, -, *, / (integer division, truncating toward zero). Returns an "
        "int. One fenced ```python``` block.",
        "when evaluate_postfix\n"
        "  assert evaluate_postfix(['2','1','+','3','*']) == 9\n"
        "  assert evaluate_postfix(['4','13','5','/','+']) == 6\n"
        "  assert evaluate_postfix(['10','6','9','3','+','-11','*','/',"
        "'*','17','+','5','+']) == 22\n"
        "  assert evaluate_postfix(['3']) == 3\n",
        "```python\n"
        "def evaluate_postfix(tokens):\n"
        "    st = []\n"
        "    for t in tokens:\n"
        "        if t in ('+','-','*','/'):\n"
        "            b = st.pop(); a = st.pop()\n"
        "            if t == '+': st.append(a + b)\n"
        "            elif t == '-': st.append(a - b)\n"
        "            elif t == '*': st.append(a * b)\n"
        "            else: st.append(int(a / b))\n"
        "        else:\n"
        "            st.append(int(t))\n"
        "    return st[-1]\n"
        "```",
        "```python\n"
        "def evaluate_postfix(tokens):\n"
        "    return sum(int(t) for t in tokens if t.lstrip('-').isdigit())\n"
        "```",
    ),
    (
        "mini_lexer",
        "Write a Python function `mini_lexer(s)` that tokenizes a string into "
        "a list of (kind, value) tuples. Kinds: 'INT' (integer literal), "
        "'ID' (identifier: starts with letter/underscore, then letters/digits"
        "/underscore), 'OP' (single char in `+-*/=()`). Whitespace is skipped. "
        "Unknown chars raise ValueError. One fenced ```python``` block.",
        "when mini_lexer\n"
        "  assert mini_lexer('x = 42 + y1') == "
        "[('ID', 'x'), ('OP', '='), ('INT', 42), ('OP', '+'), ('ID', 'y1')]\n"
        "  assert mini_lexer('  3*(a)') == "
        "[('INT', 3), ('OP', '*'), ('OP', '('), ('ID', 'a'), ('OP', ')')]\n"
        "  assert mini_lexer('') == []\n",
        "```python\n"
        "def mini_lexer(s):\n"
        "    out = []; i = 0\n"
        "    while i < len(s):\n"
        "        c = s[i]\n"
        "        if c.isspace(): i += 1; continue\n"
        "        if c.isdigit():\n"
        "            j = i\n"
        "            while j < len(s) and s[j].isdigit(): j += 1\n"
        "            out.append(('INT', int(s[i:j]))); i = j\n"
        "        elif c.isalpha() or c == '_':\n"
        "            j = i\n"
        "            while j < len(s) and (s[j].isalnum() or s[j] == '_'): j += 1\n"
        "            out.append(('ID', s[i:j])); i = j\n"
        "        elif c in '+-*/=()':\n"
        "            out.append(('OP', c)); i += 1\n"
        "        else:\n"
        "            raise ValueError(c)\n"
        "    return out\n"
        "```",
        "```python\n"
        "def mini_lexer(s):\n"
        "    return [('OP', c) for c in s if not c.isspace()]\n"
        "```",
    ),
    (
        "html_attr_parse",
        "Write a Python function `html_attr_parse(tag)` that parses an HTML "
        "open tag of the form `<name attr1=\"value1\" attr2='value2'>` into a "
        "dict {attr: value}. Values may be double- or single-quoted; the "
        "function must handle both. Returns {} for `<name>`. Assume the input "
        "is a single well-formed tag (no nested tags, no boolean attrs). "
        "One fenced ```python``` block.",
        "when html_attr_parse\n"
        "  assert html_attr_parse('<a href=\"x\" id=\"y\">') == {'href':'x','id':'y'}\n"
        "  assert html_attr_parse(\"<a class='c' id='d'>\") == {'class':'c','id':'d'}\n"
        "  assert html_attr_parse('<a>') == {}\n"
        "  assert html_attr_parse('<a href=\"x\" data-x=\\'q\\'>') == "
        "{'href':'x','data-x':'q'}\n",
        "```python\n"
        "import re\n"
        "def html_attr_parse(tag):\n"
        "    inner = tag.strip('<>').split(' ', 1)\n"
        "    if len(inner) == 1: return {}\n"
        "    body = inner[1].rstrip('>')\n"
        "    out = {}\n"
        "    for m in re.finditer(r\"([\\w-]+)=(\\\"[^\\\"]*\\\"|'[^']*')\", body):\n"
        "        out[m.group(1)] = m.group(2)[1:-1]\n"
        "    return out\n"
        "```",
        "```python\n"
        "def html_attr_parse(tag):\n"
        "    return {}  # broken: never parses\n"
        "```",
    ),
    (
        "roman_to_int",
        "Write a Python function `roman_to_int(s)` that converts a Roman "
        "numeral string (uppercase I, V, X, L, C, D, M) to an integer. "
        "Handles subtractive pairs (IV=4, IX=9, XL=40, XC=90, CD=400, "
        "CM=900). One fenced ```python``` block.",
        "when roman_to_int\n"
        "  assert roman_to_int('III') == 3\n"
        "  assert roman_to_int('IV') == 4\n"
        "  assert roman_to_int('IX') == 9\n"
        "  assert roman_to_int('LVIII') == 58\n"
        "  assert roman_to_int('MCMXCIV') == 1994\n",
        "```python\n"
        "def roman_to_int(s):\n"
        "    v = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}\n"
        "    r = 0\n"
        "    for i, ch in enumerate(s):\n"
        "        if i+1 < len(s) and v[ch] < v[s[i+1]]:\n"
        "            r -= v[ch]\n"
        "        else:\n"
        "            r += v[ch]\n"
        "    return r\n"
        "```",
        "```python\n"
        "def roman_to_int(s):\n"
        "    v = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}\n"
        "    return sum(v[c] for c in s)  # broken: ignores subtractive\n"
        "```",
    ),
]


# -- Sleep-safety ----------------------------------------------------------

@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """Hold a Windows wake-lock for the duration. Lid-close still halts CPU."""
    set_state = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if set_state is not None:
        with contextlib.suppress(Exception):
            set_state.SetThreadExecutionState(0x80000001)
    try:
        yield
    finally:
        if set_state is not None:
            with contextlib.suppress(Exception):
                set_state.SetThreadExecutionState(0x80000000)


# -- Functional gate (subprocess) ------------------------------------------

_FUNC_TIMEOUT_S = 25


def gate_functional(text: str, dsl: str) -> dict:
    """Run the candidate through the verify_functional sandbox subprocess
    with a CUSTOM DSL. Returns the same shape as the MCP tool."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_servers._funcverify_child"],
            input=json.dumps({"text": text, "dsl": dsl}),
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=_FUNC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "ran": True, "applicable": True, "passed": False, "checked": 0,
            "failures": [{"symbol": "<sandbox>", "expr": "completes",
                          "observed": f"timed out after {_FUNC_TIMEOUT_S}s",
                          "requirement": "must terminate"}],
        }
    if proc.returncode != 0 or not proc.stdout.strip():
        return {
            "ran": False, "applicable": False, "passed": False, "checked": 0,
            "failures": [{"symbol": "<sandbox>", "expr": "exits cleanly",
                          "observed": (proc.stderr or "no output").strip()[:500],
                          "requirement": "sandbox must run"}],
        }
    return json.loads(proc.stdout)


def _is_sandbox_crash(verdict: dict) -> bool:
    """The 'sandbox must run' failure mode means the DSL parse exploded; the
    candidate code is neither known-good nor known-broken. Distinguish from a
    real gate-FAIL that ran cleanly and reported assertion failures."""
    if verdict.get("ran"):
        # Even when ran=True, the timeout path uses symbol '<sandbox>'; treat
        # that the same way (uncertain on the candidate, exclude as subject).
        return any(f.get("symbol") == "<sandbox>"
                   for f in verdict.get("failures", []))
    return True


# -- DSL self-tests (run at module load) -----------------------------------

def _self_test_dsls() -> None:
    """Run every task's DSL against its known-good and known-broken impls.
    Raise AssertionError immediately if a DSL is broken -- the v1 experiment
    paid 60 trials per subject to discover this kind of bug; the v2 catches
    it at import time."""
    for slug, _prompt, dsl, good, broken in TASKS:
        gv = gate_functional(good, dsl)
        assert gv.get("passed"), (
            f"DSL self-test FAILED for {slug!r}: known-good impl rejected. "
            f"failures={gv.get('failures')}"
        )
        bv = gate_functional(broken, dsl)
        assert not bv.get("passed"), (
            f"DSL self-test FAILED for {slug!r}: known-broken impl accepted. "
            f"No failures reported -- DSL is too permissive."
        )
    print(f"[self-test] {len(TASKS)}/{len(TASKS)} DSLs verified")


# -- Phase 1: subject generation -------------------------------------------

def _load_thresholds() -> Thresholds:
    return (Thresholds.load(THRESHOLDS_PATH)
            if THRESHOLDS_PATH.exists() else Thresholds())


def phase1_generate_subjects(emit) -> list[dict]:
    """Generate NPU drafts on the battery. Subject = (gate-FAIL with at least
    one real assertion failure) AND (text_reasons non-empty). Sandbox crashes
    DON'T count as subjects (DSL bug, not draft quality). Idempotent."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if SUBJECTS_PATH.exists():
        rows = [json.loads(line) for line in SUBJECTS_PATH.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        print(f"[phase1] reusing {len(rows)} subjects from {SUBJECTS_PATH}")
        return rows

    thr = _load_thresholds()
    print("[phase1] compiling NPU worker...")
    npu = make_npu_worker()
    print(f"[phase1] NPU on {npu.device}; battery has {len(TASKS)} tasks")

    subjects: list[dict] = []
    for slug, prompt, dsl, _good, _broken in TASKS:
        kept = False
        for attempt in range(NPU_DRAFTS_PER_TASK):
            t0 = time.perf_counter()
            d = npu.draft(prompt)
            dt = time.perf_counter() - t0
            text = d.text
            gate = gate_functional(text, dsl)
            degen = check_degeneration(text, thresholds=thr)
            passed = bool(gate.get("passed"))
            crashed = _is_sandbox_crash(gate)
            text_reasons = degen.text_reasons
            emit("phase1", {
                "task": slug, "attempt": str(attempt),
                "gate_passed": str(passed).lower(),
                "sandbox_crashed": str(crashed).lower(),
                "degraded": str(bool(text_reasons)).lower(),
                "n_text_reasons": str(len(text_reasons)),
                "latency_s": f"{dt:.2f}",
                "device": npu.device,
            })
            print(f"  [{slug}] att {attempt}: pass={passed} "
                  f"crash={crashed} degen={bool(text_reasons)} "
                  f"reasons={len(text_reasons)} {dt:.1f}s")
            # Subject criterion: clean gate run (NOT a sandbox crash) that
            # reported assertion failures, AND text_reasons non-empty.
            if not passed and not crashed and text_reasons:
                subjects.append({
                    "task": slug,
                    "prompt": prompt,
                    "dsl": dsl,
                    "npu_text": text,
                    "npu_device": npu.device,
                    "failures": [
                        {"expr": f.get("expr", ""),
                         "observed": f.get("observed", ""),
                         "requirement": f.get("requirement", "")}
                        for f in gate.get("failures", [])
                    ],
                    "text_reasons": list(text_reasons),
                    "score": degen.score,
                    "features": degen.features,
                })
                kept = True
                break
        if not kept:
            print(f"  [{slug}] no subject after {NPU_DRAFTS_PER_TASK} attempts")

    SUBJECTS_PATH.write_text(
        "\n".join(json.dumps(s) for s in subjects) + "\n", encoding="utf-8")
    print(f"[phase1] wrote {len(subjects)} subjects -> {SUBJECTS_PATH}")
    return subjects


# -- Phase 2: A/B repair sweep ---------------------------------------------

def _subject_checkpoint_path(idx: int) -> Path:
    return OUT_DIR / f"subject-{idx:02d}.json"


def phase2_ab_sweep(subjects: list[dict], emit) -> list[dict]:
    """For each subject, 30 control + 30 treatment GPU repair trials,
    interleaved. Per-subject JSON checkpoint after each trial."""
    url = CONFIG.ollama_base_url.rstrip("/")
    model = CONFIG.gpu_model
    results: list[dict] = []

    for idx, subj in enumerate(subjects):
        ckpt_path = _subject_checkpoint_path(idx)
        if ckpt_path.exists():
            ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
            if ckpt.get("done"):
                print(f"[phase2] subject {idx} ({subj['task']}) "
                      f"already complete; skipping")
                results.append(ckpt)
                continue
        else:
            ckpt = {
                "subject_idx": idx,
                "task": subj["task"],
                "text_reasons": subj["text_reasons"],
                "trials": [],
                "done": False,
            }

        fails = [CheckFailure(
            expr=f["expr"], observed=f["observed"],
            requirement=f.get("requirement", "")
        ) for f in subj["failures"]]
        control_prompt = build_repair_prompt(
            subj["prompt"], subj["npu_text"], fails, degen_reasons=())
        treatment_prompt = build_repair_prompt(
            subj["prompt"], subj["npu_text"], fails,
            degen_reasons=tuple(subj["text_reasons"]))

        schedule = []
        for _ in range(N_TRIALS_PER_ARM):
            schedule.append(("ctrl", control_prompt))
            schedule.append(("trt", treatment_prompt))

        completed = len(ckpt["trials"])
        print(f"[phase2] subject {idx} ({subj['task']}): "
              f"{completed}/{len(schedule)} trials done; resuming...")

        for trial_idx in range(completed, len(schedule)):
            arm, prompt = schedule[trial_idx]
            t0 = time.perf_counter()
            r = gpu_generate(url, model, prompt, REPAIR_MAX_TOKENS)
            dt = time.perf_counter() - t0
            if not r.available:
                trial = {
                    "trial": trial_idx, "arm": arm, "available": False,
                    "passed": False, "latency_s": dt,
                    "tokens_per_s": 0.0, "text_len": 0,
                }
            else:
                gate = gate_functional(r.text, subj["dsl"])
                trial = {
                    "trial": trial_idx, "arm": arm, "available": True,
                    "passed": bool(gate.get("passed")),
                    "latency_s": r.latency_s,
                    "tokens_per_s": r.tokens_per_s,
                    "text_len": len(r.text),
                }
            ckpt["trials"].append(trial)
            ckpt_path.write_text(json.dumps(ckpt), encoding="utf-8")
            emit("phase2", {
                "subject_idx": str(idx), "task": subj["task"],
                "trial": str(trial_idx), "arm": arm,
                "passed": str(trial["passed"]).lower(),
                "available": str(trial["available"]).lower(),
                "latency_s": f"{trial['latency_s']:.2f}",
                "tokens_per_s": f"{trial['tokens_per_s']:.1f}",
                "text_len": str(trial["text_len"]),
            })
            print(f"  s{idx:02d}/{subj['task']} t{trial_idx:02d} "
                  f"{arm}: pass={trial['passed']} {dt:.1f}s")

        ckpt["done"] = True
        ckpt_path.write_text(json.dumps(ckpt), encoding="utf-8")
        results.append(ckpt)

    return results


# -- Phase 3: Bayesian-MC analysis -----------------------------------------

def _beta_ci(a: int, b: int, draws: int, rng: random.Random) -> dict:
    samples = [rng.betavariate(1 + a, 1 + b) for _ in range(draws)]
    samples.sort()
    lo = samples[int(draws * 0.025)]
    hi = samples[int(draws * 0.975)]
    return {"mean": sum(samples) / draws, "lo95": lo, "hi95": hi,
            "samples": samples}


def _p_a_gt_b(samples_a: list[float], samples_b: list[float]) -> float:
    n = min(len(samples_a), len(samples_b))
    wins = sum(1 for i in range(n) if samples_a[i] > samples_b[i])
    return wins / n


def phase3_analyze(checkpoints: list[dict]) -> dict:
    rng = random.Random(MC_SEED)
    per_subject = []
    pooled_ctrl_pass = pooled_ctrl_fail = 0
    pooled_trt_pass = pooled_trt_fail = 0

    for ckpt in checkpoints:
        ctrl_pass = sum(1 for t in ckpt["trials"]
                        if t["arm"] == "ctrl" and t["passed"])
        ctrl_fail = sum(1 for t in ckpt["trials"]
                        if t["arm"] == "ctrl" and not t["passed"])
        trt_pass = sum(1 for t in ckpt["trials"]
                       if t["arm"] == "trt" and t["passed"])
        trt_fail = sum(1 for t in ckpt["trials"]
                       if t["arm"] == "trt" and not t["passed"])
        ctrl_post = _beta_ci(ctrl_pass, ctrl_fail, MC_DRAWS, rng)
        trt_post = _beta_ci(trt_pass, trt_fail, MC_DRAWS, rng)
        p = _p_a_gt_b(trt_post["samples"], ctrl_post["samples"])
        per_subject.append({
            "subject_idx": ckpt["subject_idx"],
            "task": ckpt["task"],
            "n_text_reasons": len(ckpt.get("text_reasons", [])),
            "ctrl": {"pass": ctrl_pass, "fail": ctrl_fail,
                     "mean": ctrl_post["mean"],
                     "lo95": ctrl_post["lo95"], "hi95": ctrl_post["hi95"]},
            "trt": {"pass": trt_pass, "fail": trt_fail,
                    "mean": trt_post["mean"],
                    "lo95": trt_post["lo95"], "hi95": trt_post["hi95"]},
            "p_trt_gt_ctrl": p,
            "effect_pp": 100 * (trt_post["mean"] - ctrl_post["mean"]),
        })
        pooled_ctrl_pass += ctrl_pass
        pooled_ctrl_fail += ctrl_fail
        pooled_trt_pass += trt_pass
        pooled_trt_fail += trt_fail

    pooled_ctrl = _beta_ci(pooled_ctrl_pass, pooled_ctrl_fail, MC_DRAWS, rng)
    pooled_trt = _beta_ci(pooled_trt_pass, pooled_trt_fail, MC_DRAWS, rng)
    pooled_p = _p_a_gt_b(pooled_trt["samples"], pooled_ctrl["samples"])
    pooled_effect_pp = 100 * (pooled_trt["mean"] - pooled_ctrl["mean"])

    if pooled_p >= SHIP_P and pooled_effect_pp >= SHIP_EFFECT_PP:
        verdict = "SHIP_NEXT_LEVER"
    elif pooled_p <= REVERT_P:
        verdict = "REVERT"
    else:
        verdict = "MARGINAL_INVESTIGATE"

    summary = {
        "topic": TOPIC,
        "n_subjects": len(per_subject),
        "n_trials_per_arm": N_TRIALS_PER_ARM,
        "mc_draws": MC_DRAWS,
        "mc_seed": MC_SEED,
        "pooled": {
            "ctrl_pass": pooled_ctrl_pass, "ctrl_fail": pooled_ctrl_fail,
            "trt_pass": pooled_trt_pass, "trt_fail": pooled_trt_fail,
            "ctrl_mean": pooled_ctrl["mean"],
            "ctrl_lo95": pooled_ctrl["lo95"], "ctrl_hi95": pooled_ctrl["hi95"],
            "trt_mean": pooled_trt["mean"],
            "trt_lo95": pooled_trt["lo95"], "trt_hi95": pooled_trt["hi95"],
            "p_trt_gt_ctrl": pooled_p,
            "effect_pp": pooled_effect_pp,
        },
        "per_subject": [
            {k: v for k, v in row.items() if k != "samples"}
            for row in per_subject
        ],
        "verdict": verdict,
        "decision_rule": {
            "ship_p": SHIP_P, "ship_effect_pp": SHIP_EFFECT_PP,
            "revert_p": REVERT_P,
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[phase3] wrote summary -> {SUMMARY_PATH}")
    print(f"[phase3] pooled: ctrl={pooled_ctrl_pass}/"
          f"{pooled_ctrl_pass + pooled_ctrl_fail}  "
          f"trt={pooled_trt_pass}/{pooled_trt_pass + pooled_trt_fail}  "
          f"P(trt>ctrl)={pooled_p:.3f}  effect={pooled_effect_pp:+.1f}pp  "
          f"verdict={verdict}")
    return summary


# -- Main ------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=["self-test", "1", "2", "3", "all"],
                    default="all", help="run a single phase or all")
    args = ap.parse_args(argv)

    # The self-test runs on every invocation EXCEPT a bare --phase 3 (Phase 3
    # is the deterministic analyzer and doesn't need a live sandbox).
    if args.phase != "3":
        _self_test_dsls()
    if args.phase == "self-test":
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    emit = make_experiment_recorder(TOPIC)

    with keep_awake():
        if args.phase in ("1", "all"):
            subjects = phase1_generate_subjects(emit)
        else:
            subjects = [json.loads(line)
                        for line in SUBJECTS_PATH.read_text(
                            encoding="utf-8").splitlines() if line.strip()]

        if args.phase in ("2", "all"):
            checkpoints = phase2_ab_sweep(subjects, emit)
        else:
            checkpoints = []
            for i in range(len(subjects)):
                p = _subject_checkpoint_path(i)
                if p.exists():
                    checkpoints.append(json.loads(p.read_text(encoding="utf-8")))

        if args.phase in ("3", "all"):
            phase3_analyze(checkpoints)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
