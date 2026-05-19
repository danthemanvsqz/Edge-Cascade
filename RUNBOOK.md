# edge-cascade runbook — for the launched Claude (Tier 3)

This is the operational checklist to run when the user launches the project via
`scripts/edge-cli.ps1`. You are **Tier 3**. Follow `CLAUDE.md` for the
delegation policy; this runbook is the *validation* sequence proving the mesh
works before trusting it on real project work.

> Honesty rule (non-negotiable): every claim about which tier did what MUST be
> backed by the `runs/<server>.rec` recorder, not your own narration. After any
> delegated step, a fresh `.rec` for the server you used is the proof; a stale
> `.rec` for `edge-gpu`/`edge-cloud` proves work stayed cheap. Never state a
> tier ran unless its `.rec` grew.

## 0. Preflight (always)

1. `/mcp` → expect exactly **3** servers, all `✔ connected`: `edge-npu` (3
   tools), `edge-gpu` (2), `edge-verify` (3); **no `edge-cloud`** (paid tier is
   excluded by the launcher — confirm it is absent).
2. First `edge-npu` call compiles the NPU (~12–21 s, one-time). That pause is
   expected, not a hang.

## A. Headless full-loop regression (`probe_repair_path.py`)

Run from a normal shell, not inside the session (Ollama must be up with
`qwen2.5-coder:14b` for Phase 2; Phase 1 runs without it):

```
uv run python scripts/probe_repair_path.py
```

This drives the **entire local loop** — `edge-npu` → `edge-verify` →
`edge-gpu` → re-gate → 2-round cap — with no session and no paid tier.

Expected (verified 2026-05-18, three runs):
- `edge-npu.route` ~0.65/standard (1.5B over-rates short prompts — advisory).
- `@640` NPU draft parses but `verify_functional` **fails** `KeyError: 'E'`
  (the sink-node bug `checks.dsl::drone_ok` is built to catch).
- `repair_prompt` (well-formed) → `edge-gpu.generate` repair → re-gate.
- **Stochastic outcome, both valid:** the 14B sometimes fixes it in round 1
  (`[OK] ANSWERED BY GPU`); sometimes misses it twice → `[CAP]` → Tier-3
  handoff point (bounded, NOT an infinite loop). Both prove the loop.
- `runs/edge-npu.rec` + `edge-verify.rec` + `edge-gpu.rec` fresh;
  `edge-cloud.rec` **stale** (zero spend) — confirm this.

If the Tier-1 gate does **not** fail, stop — the verifier is broken; do not
trust any "verified" claim downstream.

## B. Same task through the real orchestrator (in-session confirmation)

Test A proves the *mechanism* headlessly. Test B confirms the *launched
Claude* actually drives it per policy. In the session, give this verbatim:

> Write a Python function `def dijkstra(graph, start)` that returns a dict of
> shortest-path costs from `start` for a directed weighted graph given as
> `{node: {neighbor: weight}}`.

Expected chain (emit a `routing_dispatch` block per `CLAUDE.md`):

1. `edge-npu.route` → `edge-npu.draft`.
2. `edge-verify.verify_syntax` + `verify_functional` → **fail** (`drone_ok`).
3. `edge-verify.repair_prompt` → escalate: `edge-gpu.generate` with
   `prior_attempt` = the failed draft.
4. Re-gate the GPU output with `edge-verify`.
5. Pass → done (answered by Tier 2). Fail again → **one** more repair round,
   then per policy **you (Tier 3) take it over and fix it yourself** — cloud is
   off; that is the designed terminal, not a dead end.

Success criteria (check `.rec` yourself, report counts honestly):
- `edge-npu.rec` **and** `edge-gpu.rec` both fresh (real escalation, not faked).
- `edge-verify.rec` shows a `verify_functional` `passed:false` then a later
  `passed:true` (or your own Tier-3 fix verified).
- `edge-cloud.rec` **stale** — zero spend.
- Your narration's tool counts match the `.rec` counts exactly.

## C. At-a-glance verification (replay + dashboard)

These operationalize the honesty rule — they read `runs/*.rec` directly, so
they are the proof, not your narration. Both are read-only and need no
hardware.

- **`uv run python replay.py --last 1`** — after any delegated step, this
  prints the *actual* hop sequence of the most recent episode (`route →
  draft → verify → repair_prompt → gpu.generate → …`). Use it to confirm what
  you claim happened really did, in that order. `--failures-only` isolates
  the gate failures; `--run <id>` / `--server edge-gpu` narrow it.
- **`uv run python dashboard.py`** — a live health view to keep open in a
  second pane during a session (`--once` for a one-shot, `--json` for a
  scripted check). The **SPEND** panel is the load-bearing one: it must read
  `edge-cloud calls=0  total=$0.00  OK`. If it ever goes RED / `NONZERO !`,
  the local-first invariant broke — treat any "stayed cheap" claim as false
  and stop (this is the same condition as the `edge-cloud` stop below).

## Known issues to expect (don't re-discover them)

- **Router miscalibration:** ~0.65/"standard" for almost everything, ~0.85+
  and "hard" for short/conversational input. Treat `route()` as advisory; enter
  at the lowest plausible tier and climb only on a gate failure.
- **NPU 192-token truncation:** at the default `npu_max_new_tokens`, code drafts
  often truncate and fail the *syntax* gate (not a logic problem). Prefer
  requesting a larger `max_tokens` on `edge-npu.draft` for code generation
  (~640 produced complete code in testing).
- **`repair_prompt` input:** pass the *extracted* code block, not the raw
  prose-wrapped draft, to avoid nested-fence noise in the repair request.
- **`verify_functional` `applicable:false`** means no `checks.dsl` block matched
  the defined symbols → it degrades to syntax-gate-only. That is not a pass;
  review the logic yourself (Tier 3) before trusting it.

## Stop conditions

- `/mcp` shows `edge-cloud` present → abort: the launcher's spend guard failed.
- Any tier's narration not backed by a fresh `.rec` → treat the answer as
  unverified and say so.
- Repair loop exceeds 2 rounds without passing → take it over yourself; do not
  loop further.
