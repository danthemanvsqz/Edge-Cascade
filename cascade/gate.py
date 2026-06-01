"""Language-keyed verifier registry — single source of truth for gate dispatch.

Replaces the hardcoded if/elif chain in topologies_canvas._gate. Adding a new
language is three lines: update _LANG_MAP, call register(), done — no edits to
the Canvas pipeline.
"""
from __future__ import annotations

import concurrent.futures
import re
from collections.abc import Callable

from cascade.verifier import Verdict

LanguageVerifier = Callable[[str], Verdict]

_REGISTRY: dict[str, LanguageVerifier] = {}
_LANG_MAP: dict[str, str] = {
    "python": "python",
    "py": "python",
    "typescript": "typescript",
    "ts": "typescript",
    "javascript": "javascript",
    "js": "javascript",
    "git": "git",
    "bash": "bash",
    "sh": "bash",
    "shell": "bash",
}

# Matches the opening fence; captures the optional language tag (empty = bare).
_FENCE_TAG = re.compile(r"```(\w*)\s*\n", re.IGNORECASE)

# Languages tried when no fence tag is present (ambiguous text).
# Update this list when a new verifier is registered (VR-3 adds javascript).
_AMBIGUOUS_CANDIDATES: list[str] = ["python", "typescript", "javascript"]


def register(lang: str, fn: LanguageVerifier) -> None:
    """Register a verifier for a canonical language name."""
    _REGISTRY[lang] = fn


def detect_language(text: str) -> str:
    """Return the canonical language of the first fenced block in *text*.

    - Named fence (```python, ```git …) → _LANG_MAP lookup
    - Unknown tag (```ruby …)           → "unknown-<tag>"
    - Bare fence (``` no tag)            → "python"  (backward compat)
    - No fence at all                    → "ambiguous"
    """
    m = _FENCE_TAG.search(text)
    if m is None:
        return "ambiguous"
    tag = m.group(1).lower()
    if not tag:
        return "python"
    return _LANG_MAP.get(tag, f"unknown-{tag}")


def gate(text: str, dsl: str | None) -> tuple[bool, list]:
    """Gate *text* against the appropriate verifier.

    Returns ``(passed, failures)`` where *failures* is a JSON-clean list of
    dicts safe to carry across the Celery broker envelope.

    Dispatch order:
    1. DSL present → functional gate (cascade.tasks.verify_functional),
       language-agnostic (the functional gate is Python-DSL only).
    2. Language == "ambiguous" (no fence) → gate_any over common languages.
    3. Language starts with "unknown-" → explicit failure.
    4. Language mapped but not registered → explicit failure.
    5. Happy path → call the registered verifier.
    """
    if dsl:
        import cascade.tasks  # lazy — must stay here; top-level import creates a cycle
        v = cascade.tasks.verify_functional(text, dsl)
        return bool(v.get("passed")), list(v.get("failures", ()))
    lang = detect_language(text)
    if lang == "ambiguous":
        return gate_any(text, _AMBIGUOUS_CANDIDATES)
    if lang.startswith("unknown-"):
        return False, [
            {
                "language": lang,
                "expr": "unknown-language",
                "observed": lang,
                "requirement": (
                    f"register a verifier for {lang!r} via "
                    "cascade.gate.register(lang, fn)"
                ),
            }
        ]
    if lang not in _REGISTRY:
        return False, [
            {
                "language": lang,
                "expr": "no-verifier",
                "observed": lang,
                "requirement": (
                    f"{lang!r} is in _LANG_MAP but has no registered verifier"
                ),
            }
        ]
    verdict = _REGISTRY[lang](text)
    if verdict.passed:
        return True, []
    return False, [
        {
            "language": lang,
            "expr": f"{lang}-syntax",
            "observed": verdict.reason,
            "requirement": f"fenced {lang} block that parses",
        }
    ]


def gate_any(text: str, langs: list[str]) -> tuple[bool, list]:
    """OR-gate: pass if *any* registered verifier in *langs* passes.

    Runs all registered verifiers concurrently. Skips languages not yet in
    _REGISTRY. Returns the combined failure list if all fail.
    """
    registered = [(lang, _REGISTRY[lang]) for lang in langs if lang in _REGISTRY]
    if not registered:
        return False, [
            {
                "language": "ambiguous",
                "expr": "no-registered-verifiers",
                "observed": str(langs),
                "requirement": "at least one registered verifier in the candidate list",
            }
        ]

    def _run(lang: str, fn: LanguageVerifier) -> tuple[bool, str, str]:
        verdict = fn(text)
        return verdict.passed, lang, verdict.reason

    all_failures: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor() as pool:
        futures = {pool.submit(_run, lang, fn): lang for lang, fn in registered}
        for fut in concurrent.futures.as_completed(futures):
            passed, lang, reason = fut.result()
            if passed:
                return True, []
            all_failures.append(
                {
                    "language": lang,
                    "expr": f"{lang}-syntax",
                    "observed": reason,
                    "requirement": f"fenced {lang} block that parses",
                }
            )
    return False, sorted(all_failures, key=lambda f: f["language"])


# ---------------------------------------------------------------------------
# Self-registration. Python + TypeScript are always available.
# Shell/git (VR-2) and JavaScript (VR-3) register in their own modules.
# ---------------------------------------------------------------------------
from cascade.ts_verifier import verify_ts as _ts_verify  # noqa: E402
from cascade.verifier import verify as _py_verify  # noqa: E402

register("python", _py_verify)
register("typescript", _ts_verify)
