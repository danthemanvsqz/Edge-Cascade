# Design: Language-keyed Verifier Registry

**Status:** Planned — see BACKLOG items #VR-1 through #VR-5
**Motivation:** BACKLOG item analysis — 23 caps wasted 19 min/97 routes;
git commands mandatory pipeline artifacts (PR #138) but always cap because
there is no gate that accepts them; `_gate` dispatch is hardcoded and untested.

---

## Problem statement

The current escalation gate (`_gate` in `cascade/topologies_canvas.py`,
`coverage.omit`) is a hardcoded if/elif chain:

```
DSL present → functional gate (tasks.verify_functional)
is_typescript → TS gate (ts_verifier.verify_ts)
else         → Python gate (tasks.verify_syntax)
```

Consequences:
- **Git/CLI always caps.** A `git commit -m "..."` draft fails the Python AST
  check. Now that git commands are mandatory pipeline artifacts (PR #138), every
  git route is a guaranteed LOSE. That is 19+ min of wasted GPU wall time per
  97-route sample, and the scoreboard will degrade further.
- **JavaScript unroutable.** No JS verifier; JS drafts fail the Python gate.
- **Dispatch is in `coverage.omit`.** The only gate-dispatch tests are eager
  behavioral tests; the logic itself has no unit coverage.
- **Opaque failures.** `"no fenced code block"` regardless of artifact type;
  the repair prompt has no language context to act on.
- **Hard to extend.** Adding a language means editing `topologies_canvas.py`
  (coverage-omit, live-validated only).

---

## Design

### Core: `cascade/gate.py` (NEW, fully covered)

Single source of truth for all gate dispatch. Replaces the `_gate` body in
`topologies_canvas.py` and the `tasks.verify_syntax` call in `wiring.py`.

```python
LanguageVerifier = Callable[[str], Verdict]    # text → Verdict
_REGISTRY: dict[str, LanguageVerifier]         # lang → callable
_LANG_MAP: dict[str, str]                      # fence tag → canonical lang name

def register(lang: str, fn: LanguageVerifier) -> None: ...
def detect_language(text: str) -> str: ...
def gate(text: str, dsl: str | None) -> tuple[bool, list[dict]]: ...
def gate_any(text: str, langs: list[str]) -> tuple[bool, list[dict]]: ...
```

#### `detect_language(text)` logic

| Input | Result |
|-------|--------|
| Named fence ` ```python ` / ` ```git ` / etc. | `_LANG_MAP.get(tag, "unknown-<tag>")` |
| Bare fence ` ``` ` (no tag) | `"python"` — backward compat, current default |
| No fence at all | `"ambiguous"` → triggers `gate_any` |

#### `_LANG_MAP`

```python
_LANG_MAP = {
    "python": "python", "py": "python",
    "typescript": "typescript", "ts": "typescript",
    "javascript": "javascript", "js": "javascript",
    "git": "git",
    "bash": "bash", "sh": "bash", "shell": "bash",
}
```

#### `gate()` dispatch

```
dsl present        → tasks.verify_functional (unchanged)
lang == ambiguous  → gate_any(text, ["python", "typescript", "javascript"])
lang == unknown-X  → explicit failure: "no verifier for 'X'; register via cascade.gate.register('X', fn)"
lang not in registry → explicit failure: "'X' in _LANG_MAP but not registered"
happy path         → _REGISTRY[lang](text) → Verdict → (bool, [{language, expr, observed, requirement}])
```

#### `gate_any()` — parallel OR-gate

Uses `concurrent.futures.ThreadPoolExecutor`. Runs all listed verifiers
concurrently; passes if any pass (OR semantics); returns all failures if all
fail. Only fires when `detect_language` returns `"ambiguous"` (no fence tag).

#### Self-registration (bottom of `gate.py`)

```python
from cascade.verifier import verify as _py_verify
from cascade.ts_verifier import verify_ts as _ts_verify
from cascade.shell_verifier import verify_git, verify_shell
from cascade.js_verifier import verify_js

register("python", _py_verify)
register("typescript", _ts_verify)
register("git", verify_git)
register("bash", verify_shell)
register("javascript", verify_js)
```

#### Language onboarding story

```python
# Add Ruby support:
from cascade.gate import register, _LANG_MAP
_LANG_MAP["ruby"] = "ruby"
register("ruby", my_ruby_verifier)
```
That is the full onboarding surface. No edits to `topologies_canvas.py`.

---

### `cascade/shell_verifier.py` (NEW, covered)

```python
_GIT_FENCE  = re.compile(r"```(?:git)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_BASH_FENCE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_GIT_CMD    = re.compile(r"^git\s+\S+", re.MULTILINE)

def extract_git(text: str) -> str | None   # longest ```git block
def extract_shell(text: str) -> str | None  # longest ```bash/sh/shell block

def verify_git(text: str) -> Verdict
    # No block       → Verdict(False, False, "no fenced git block in response")
    # Doesn't match  → Verdict(False, True,  "expected 'git <verb>', got: ...")
    # Matches        → Verdict(True,  True,  "git command valid")

def verify_shell(text: str) -> Verdict
    # No block      → Verdict(False, False, "no fenced bash block in response")
    # bash -n PASS  → Verdict(True,  True,  "shell syntax valid")
    # bash -n FAIL  → Verdict(False, True,  "syntax error: <stderr>")
    # bash unavail  → Verdict(False, True,  "bash unavailable: <exc>")  ← fail-soft
```

**Windows / `bash -n` note:** `bash` may not be on PATH (WSL not guaranteed).
`verify_shell` must fail-soft with `"bash unavailable: ..."` when the subprocess
cannot be found. This is the same fail-soft pattern as `ts_verifier.verify_ts`.

---

### `cascade/js_verifier.py` (NEW, covered)

```python
_JS_FENCE = re.compile(r"```(?:javascript|js)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

def extract_js(text: str) -> str | None

def verify_js(text: str) -> Verdict
    # No block    → Verdict(False, False, "no fenced javascript block in response")
    # node PASS   → Verdict(True,  True,  "javascript syntax valid")
    # node FAIL   → Verdict(False, True,  "<node error, first relevant line>")
    # node unavail → Verdict(False, True,  "node unavailable: <exc>")   ← fail-soft
```

Command: `subprocess.run(["node", "--check", "--input-type=commonjs"],
input=code, capture_output=True, text=True, timeout=20)`

Reuses `node` already present for `ts_verifier`. No new npm deps.

---

### Wiring changes

#### `cascade/topologies_canvas.py` (coverage-omit, 1-line body swap)

```python
from cascade.gate import gate as _gate_impl

def _gate(text: str, dsl: str | None) -> tuple[bool, list]:
    return _gate_impl(text, dsl)
```

`_pick_decision` and `_verify_step` continue using `_gate` unmodified.

#### `cascade/wiring.py` (covered, small change)

Replace `tasks.verify_syntax(text)` with `cascade.gate.gate(text, dsl=None)`.
Return type stays `mesh.GateInfo`; failures list converted to `CheckFailure` tuples.

---

### Failure shape

Old: `{"expr": "syntax", "observed": "...", "requirement": "..."}`
New: `{"language": "python", "expr": "python-syntax", "observed": "...", "requirement": "..."}`

The `language` key is new. Repair prompt builder can surface it in the
repair instruction without a `CheckFailure` dataclass change — the `expr` value
is already self-documenting (`"git-syntax"`, `"bash-syntax"`, `"javascript-syntax"`).

---

## Slices and prioritization

Ordering follows the 4×4 Impact×Severity matrix (impact-desc, severity-asc).

```
         I1  I2                I3            I4
S2  ✗       #VR-3 JS          #VR-1 Gate   —
            #VR-5 RP          #VR-2 Shell
S3  ✗       —                 #VR-4 Wire   —
S4  ✗       ⏳ none            ⏳ none      —
```

| # | Slice | Files | I·S | Notes |
|---|-------|-------|-----|-------|
| VR-1 | Language registry `gate.py` | `cascade/gate.py` + `tests/test_gate.py` | I3·S2 | Foundation; additive, no behavior change until VR-4 |
| VR-2 | Shell/git verifier | `cascade/shell_verifier.py` + tests | I3·S2 | Fixes git-cap; structural check, no exec |
| VR-4 | Wire call sites | `topologies_canvas.py` + `wiring.py` | I3·S3 | Makes VR-1/2/3 live; hot-path change, needs parity check |
| VR-3 | JS verifier | `cascade/js_verifier.py` + tests | I2·S2 | New capability; same `node` subprocess as ts_verifier |
| VR-5 | Repair prompt `language` field | `cascade/feedback.py` | I2·S2 | Better repair context; additive |

**Implementation order:** VR-1 first (registry must exist before anything registers).
VR-2 and VR-3 can be fanned out in parallel once VR-1 is in main. VR-4 is sequential
after VR-1/2 (the wiring is only useful once the verifiers are real). VR-5 is independent
optional polish.

**Next pick: VR-1** (I3·S2, safest). Then VR-2 ‖ VR-3 (fanout). Then VR-4.

---

## Verification gate

After VR-4 merges, run the live parity check:

```powershell
$env:CASCADE_GPU_BACKEND='llama_cpp'
uv run python scripts/parity_batch.py --backend llama_cpp
```

Case B (dijkstra, Python) must stay within ±20% of Ollama baseline (37.3s).
Additionally, route a git task and confirm `done: WIN` in `cascade.rec`:

```powershell
uv run python scripts/mesh_solve_canvas.py --topology budget `
  "git command to stage all modified files and commit with message 'refactor gate'"
```

---

## What does not change

- `cascade.tasks.verify_functional` — unchanged, still the DSL/functional gate
- `cascade.tasks.verify_syntax` — kept; wiring.py stops calling it but it remains
- `cascade/verifier.py`, `cascade/ts_verifier.py` — no edits; imported by `gate.py`
- Canvas chain shape, Celery task names, broker contract — all unchanged
- `low_latency_pick._pick_decision(gate_fn=_gate)` — still gets the same thin wrapper
- `dsl_from_cases`, `verify_dsl` aliases — unaffected
