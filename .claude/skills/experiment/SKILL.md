---
name: experiment
description: >-
  Protocol + procedure for running a LOCAL ($0, Ollama) experiment, benchmark, or
  ablation in edge-cascade. Use when designing, running, or writing up an
  experiment on the local mesh: evidence-branch hygiene, Bayesian-Monte-Carlo
  analysis, difficulty calibration, sleep-safe long runs (keep_awake), segregated
  telemetry, and findings that leave via a clean commit citing the evidence sha.
  Invoke via /experiment, or when the user says run / design / write up an
  experiment, bench, ablation, or sweep.
---

# Experiment protocol

Local inference is **$0** and latency is **not** a priority, so experiments run
MANY stochastic trials and are reasoned about Bayesianly. The point of an
experiment is durable EVIDENCE and a clear DECISION — not shipped code.

## 0. On invocation (`/experiment <idea>`) — design first, in PLAN MODE

The argument is the experiment **idea**, not a go-ahead to run it. Do **not**
create a branch or call a model yet.

1. **Enter plan mode immediately** (`EnterPlanMode`) — everything below is
   design, not execution. (This is the `pacing-small-reviewable-increments` /
   propose-then-pause rule made structural: never run a design in the same turn.)
2. **Classify the experiment** so the plan picks the right rigor:
   - *quantitative* (benchmark / ablation / routing / repair) → full §3–§4
     rigor: difficulty calibration → 30+ trials/cell → Bayesian posteriors.
   - *qualitative / creative* (e.g. a cross-tier **persona debate** — NPU=Tier 1
     `edge-npu`, GPU=Tier 2 `edge-gpu` — or any generation study) → the
     deliverable is the transcript + a stated finding; skip the posterior table
     where there is no pass/fail to count, but KEEP the safety, sleep, and
     telemetry rules (§1, §2, §5).
3. **Draft the design** against §1–§7: the hypothesis/decision it resolves, the
   `experiment/<topic>-<YYYY-MM-DD>` branch name, which tiers/models it drives,
   the trial budget, the telemetry lane, and the write-up target.
4. **Present it with `ExitPlanMode` and STOP** for approval. Only *after* the
   user approves do you create the evidence branch and execute §1 onward.

## 1. Evidence branch (SAFETY — do not violate)

- Run on a **labeled LOCAL branch**: `experiment/<topic>-<YYYY-MM-DD>`.
- **NEVER merge and NEVER delete it** — the branch *is* the raw evidence.
- **Don't push.** If it ends up pushed, immediately **CLOSE** any PR (don't merge)
  to freeze the commits.
- Findings **leave** the branch only via a **clean commit + PR to `main` that
  CITES the branch + sha** (`docs/findings/FINDINGS-<topic>.md`). Doc-only → no reviewer
  spend.
- `runs/` artifacts are gitignored → `git add -f` the result JSON/`.rec` onto the
  evidence branch (the prior evidence branches did this).

## 2. Sleep-safe long runs (the box is a LAPTOP)

Any unattended run > ~5 min MUST hold a wake-lock, or idle-sleep pauses the
process and can drop an in-flight Ollama call (→ false failures).
- **Build it in:** wrap the run loop in a `keep_awake()` context manager —
  `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` = `0x80000001`,
  release with `0x80000000`. Exemplar: `scripts/complementarity_bench.py`.
- **Already-running job with no keep_awake:** launch an external wake-lock
  sentinel (a separate process holding `0x80000001` that exits — releasing — when
  the result file appears or after a cap).
- **Laptop caveat:** the wake-lock stops *idle* sleep only; a manual sleep or
  **lid-close** halts the CPU regardless. Tell the user to leave the lid open, or
  set `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0; powercfg
  /setactive SCHEME_CURRENT` — **don't change that system setting without asking.**

## 3. Calibrate difficulty FIRST (measure, don't guess)

Hard-won lesson (AI-4: we thought dijkstra hard — it's 29/30 with repair; and
AVLTree fixable — it's a 0/30 wall). Before a capability/repair experiment,
**measure** the model's actual pass-rate on candidate tasks and bucket them:
- **too-easy** (~100%) → no failure signal (but a good CLEAN BASELINE for
  noise/ambiguity studies);
- **failure-rich** (~20–80%) → the gold band for repair/routing studies;
- **ceiling** (~0%) → escalation-only (cloud), not locally fixable.
For `qwen2.5-coder:14b`: standard data-structure/algorithm tasks are too easy;
**parser/interpreter-class tasks are its frontier.** Exemplar:
`scripts/context_precision_calibrate.py`.

## 4. Bayesian-Monte-Carlo analysis

Local = $0, so run 30+ trials/cell and report posteriors, **never point fractions
("3/3" is meaningless — its Beta posterior spans ~[.16,.99])**.
- pass/resolve = Bernoulli → posterior `Beta(1+successes, 1+failures)`; report the
  **mean + 95% credible interval**.
- compare models/arms via **`P(p_A > p_B)`** by sampling the two Beta posteriors.
- **complementarity / fallbacks:** PAIRED trials per task; estimate the
  conditional **`P(B resolves | A failed)`** — that's the fallback's real value
  (not the marginal).
- Pure stdlib: `random.betavariate(a, b)`; CI via sorted samples; seed the MC
  analysis for reproducibility (generation stays Ollama-stochastic).

## 5. Telemetry (segregated) + crash-safety

- Record to the **experiment lane**: `make_experiment_recorder("<topic>")` →
  `runs/experiment-<topic>.rec` (the `experiment-` prefix keeps it OUT of
  live-mesh metrics).
- Write the summary JSON **per-task (checkpoint)**, not just at the end, so an
  interruption keeps completed work.

## 6. Harness building blocks (reuse)

- Generate: `cascade.gpu_worker._generate(url, model, prompt, max_new_tokens)` →
  `GPUResult` (`available`, `text`, `tokens_per_s`, `latency_s`). Note its **180 s
  HTTP timeout** — long `<think>` models can trip it (recorded `unavailable`;
  exclude from posteriors).
- Gate: spawn `mcp_servers._funcverify_child` with `{"text", "dsl"}` — pass a
  **custom DSL override** so experiment tasks never touch production `checks.dsl`.
- Repair prompt: `cascade.feedback.build_repair_prompt(task, code, failures)`.
- Cloud token capture (paid lane, credit-guarded): `cascade.cloud_worker` →
  `CloudResult.input_tokens/output_tokens`. Measure token deltas for FREE first
  with a local stand-in (Ollama returns `prompt_eval_count`/`eval_count`).
- Exemplars: `scripts/complementarity_bench.py`,
  `scripts/context_precision_{calibrate,h1,ambiguity}.py`.

## 7. Write up & decide

`docs/findings/FINDINGS-<topic>.md`: TL;DR/decision first, the posterior table, the method,
caveats, reproduce command, and the evidence citation (branch + sha). Then a clean
commit/PR to `main`. State the **decision** the evidence supports, not just numbers.

---

Conventions this skill encodes (slim safety copies remain in memory):
`experiment-protocol-evidence-branches`, `experiment-methods-bayesian-monte-carlo`,
`experiment-machine-sleep-state`. Related: `metric-priorities-quality-cost-over-latency`,
`pacing-small-reviewable-increments`.
