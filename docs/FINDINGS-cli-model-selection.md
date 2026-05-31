# Findings: CLI & Git Command Generation — Model Selection + Skill Actions

**Branch:** `experiment/cli-model-selection-2026-05-31`
**Git experiment evidence:** `experiment/git-model-selection-2026-05-30`
**Date:** 2026-05-31

---

## TL;DR / Decision

**For git: `qwen2.5-coder:14b`** (97% Tier B). No other pulled model comes close.

**For CLI: `deepseek-coder:6.7b`** — statistically ties or beats the 14b on all
tiers (P=1.00 on Tier C) at 3.8 GB vs 9 GB. CLI commands are formulaic enough
that the smallest coder model is sufficient. Save the VRAM.

**Action items below** update the edge-cascade skill and UserPromptSubmit hook so
git/CLI command generation routes through the local GPU model instead of spending
Tier 3 (Claude subscription) tokens on something a local model handles at 97%+.

---

## Git Experiment Results (complete — 4 models × 20 tasks × 30 trials)

| Model | Tier A | Tier B | Tier C | Verdict |
|---|---|---|---|---|
| `qwen2.5-coder:14b` | 99% [97,100] | **97% [95,99]** | 84% [78,89] | **Production choice** |
| `qwen2.5-coder:7b` | 75% [68,80] | 86% [81,90] | 85% [80,90] | Lighter alternative |
| `deepseek-r1:14b` | 70% [64,76] | 61% [55,68] | 64% [57,71] | Do not use |
| `deepseek-coder:6.7b` | 50% [43,56] | 45% [38,52] | 60% [53,67] | Do not use |

Key finding: reasoning (r1) actively hurts git recall. Two complete collapses:
`undo_last_keep_staged` (0%) and `clean_untracked` (0%).

## CLI Experiment Results (complete — 4 models × 20 tasks × 30 trials)

| Model | Tier A | Tier B | Tier C | P(>base) C | Verdict |
|---|---|---|---|---|---|
| `qwen2.5-coder:14b` | 99.5% [98,100] | 99.5% [98,100] | 95.1% [91,98] | — | Baseline |
| `deepseek-coder:6.7b` | 99.5% [98,100] | 99.5% [98,100] | **99.5% [98,100]** | 1.00 | **Best — use this** |
| `qwen2.5-coder:7b` | 99.5% [98,100] | 99.5% [98,100] | **99.5% [98,100]** | 1.00 | Equivalent |
| `deepseek-r1:14b` | 83% [78,88] | 72% [66,78] | 46% [39,53] | 0.00 | Do not use |

Key finding: CLI commands are formulaic enough that all three coder models
(6.7b, 7b, 14b) are statistically equivalent — the 6.7b (3.8 GB) ties or
beats the 14b (9 GB) on Tier C (P=1.00). For CLI generation, use
`deepseek-coder:6.7b` to save VRAM. r1 collapses on complex tasks (46% Tier C)
for the same reason as git — reasoning is counterproductive for command recall.

---

## Action Items

### 1. Add `scripts/local_cmd.py` — thin NL→command endpoint

A lightweight wrapper around `_generate` + structural gate (no repair loop,
no Canvas overhead) that accepts a NL prompt and returns the best command.
This is the callable the skill and hook will reference.

```
uv run python scripts/local_cmd.py "show the last 10 commits one per line"
# -> git log --oneline -10
```

Interface:
- `--domain git|cli` to select the system prompt
- `--model` (default: qwen2.5-coder:14b)
- Exits 0 on gate pass, 1 on failure (so callers can fall through to Tier 3)

### 2. Update `.claude/skills/edge-cascade/SKILL.md`

Add a **Command generation** section alongside the existing code generation rule:

> **NL → git/CLI commands also go through the local model first.**
> Before generating a git command or shell command yourself, call:
> `uv run python scripts/local_cmd.py --domain git "<request>"`
> or `--domain cli` for general shell commands.
> Only fall back to Tier 3 (writing the command yourself) if the script
> returns exit code 1 (gate failed) or the model is unavailable.

### 3. Update the UserPromptSubmit hook message

Extend the hook context from:
> "every line of code goes through the Canvas pipeline FIRST"

To also include:
> "every git command and shell/CLI command request goes through the local
> GPU model first (`scripts/local_cmd.py`). Do not generate git or shell
> commands directly — route them."

### 4. (Follow-up) Wire `local_cmd.py` into Claude Code hooks

Once `local_cmd.py` exists, consider a PreToolUse hook on Bash tool calls
that detects git/CLI patterns and pre-validates the command through the local
model before execution. This closes the loop from NL → local model → executed.

---

## Reproduce

```bash
# Git experiment (complete)
git checkout experiment/git-model-selection-2026-05-30
uv run python scripts/git_model_bench.py --no-pull

# CLI experiment
git checkout experiment/cli-model-selection-2026-05-31
uv run python scripts/cli_model_bench.py --no-pull
```
