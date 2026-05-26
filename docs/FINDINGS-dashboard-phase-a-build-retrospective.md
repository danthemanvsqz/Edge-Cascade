# FINDINGS — Dashboard Phase A build retrospective: cascade discipline under partial-NPU degradation

**Date:** 2026-05-26 · **Hardware:** RTX 5070 Ti Laptop, 12 GB · **Substrate:** `scripts/edge-cli.ps1` wrapper (NPU + GPU + verify MCP servers wired; `edge-cloud` deliberately NOT wired)

**Evidence:**
- Build artifact: squash merge **`19d2d6b`** on `main` (PR #54's predecessor `29e072c` is the base).
- Slice lineage (originals in the reflog, branch deleted): `012f7d6 → 7ae4255 → e0671c8 → 51bb2ae → 2b75ee3 → 5ba17af → 1cf3b53 → 219fd29`.
- Cascade telemetry (gitignored, in the dashboard worktree): `runs/edge-gpu.rec` (1 record, 8729 B) + `runs/edge-npu.rec` (11 records, 6153 B). `edge-verify.rec` absent — `verify` was never called.
- Edge-cli session transcript: `~/.claude/projects/C--Users-danth-src-edge-cascade-dashboard/2c6a5b58-*.jsonl` (1.96 MB).
- Plan that drove the build: `~/.claude/plans/compressed-doodling-diffie.md`.
- Cross-session memory bridge: `~/.claude/projects/c--Users-danth-src/memory/edge-cascade-open-threads.md`.

## TL;DR — the cascade gracefully degraded; the build succeeded; one observability gap surfaced

Phase A merged in **~95 minutes** (05:50 → 07:25 PT) across 7 slices + 1 cleanup commit, ~2.8K LOC + 67 vitest tests, **$0 cloud spend** (Tier-4 not wired). The interesting finding, hidden inside the cascade telemetry: **Tier-1 (NPU) was unavailable for the entire build** — every `edge-npu` call returned `ok:true` at the MCP layer but `available:false` in the payload (`RuntimeError: No OpenVINO device could load the Tier-1 model: openvino_genai is required...`). The cascade gracefully failed over; Tier-2 (GPU) carried exactly one bulk-algorithmic dispatch; Tier-3 (the agent) carried everything else. **The build's `$0 cloud, no faults` invariant held under environmental degradation.**

The accidental observability gap: a periodic cascade health-probe (the 10 paired `route()` + `draft()` calls firing every ~8-10 min on a canned dijkstra prompt) reported `ok:true` at the call layer while every component was `available:false` — a yellow signal that read as green.

| Tier | Calls | Productive | Notes |
|---|---:|---:|---|
| 1 NPU | 11 | 0 | All `available:false`. 1 real `route()` for Slice 2 (escalated); 10 background health-probes (paired `route()`+`draft()` every ~8-10 min, identical dijkstra prompt). |
| 2 GPU | 1 | 1 | Slice-2 TS port. 36-second `generate` on `qwen2.5-coder:14b` producing a 4065-char TS draft. Tier-3 patched 2 strict-mode bugs (per PR body — "~95% correct"). |
| 3 Tier-3 (agent) | n/a | Slices 1, 3, 4, 5, 6, 7 + cleanup commit | Architecture, decomposition, integration, tests, UI wiring, debugging. |
| 4 Cloud | 0 | 0 | Structurally impossible — `edge-cloud` not in `edge-cli.ps1`'s server list. |

**Decision the evidence supports:** the cascade's design intent — "climb only on failure, cheapest sufficient tier" — got an unintended real test (Tier-1 down). The build succeeded *because* Tier-3 is the canonical fallback and the GPU dispatch was perfectly scoped (a documented function port). **The cross-session memory bridge proved durable through three independent shocks** (Tier-1 outage, github TCP block, Tier-3 process crash).

## Per-slice timing + cascade allocation

| # | Sha | Time (PT) | Δ from prior | Tier used | What |
|---|---|---|---|---|---|
| 1 | `012f7d6` | 06:06:37 | (16m from launch) | T3 | npm workspace at repo root + `dashboard/` scaffold |
| 2 | `7ae4255` | 06:19:49 | +13m | **T2 draft + T3 patch** | TS port of `parse_stream_incremental` + Python-golden fixture (10 vitest) |
| 3 | `e0671c8` | 06:23:52 | +4m | T3 | Multi-file `.rec` tailer (12 vitest, *inode-tracking carry-forward filed*) |
| 4 | `51bb2ae` | 06:52:06 | +28m | T3 | Derived store (21 vitest — the biggest test fan-out) |
| 5 | `2b75ee3` | 07:00:25 | +8m | T3 | Server + page + tailer↔hub wiring (10 vitest) |
| 6 | `5ba17af` | 07:08:13 | +8m | T3 | Cascade-flow SVG + theme (6 vitest, **live browser smoke ✓**) |
| 7 | `1cf3b53` | 07:16:02 | +8m | T3 | seed_replay + e2e + inode tracking fold-in (8 vitest incl. 1 real-WS e2e) |
| — | `219fd29` | 07:16:36 | +34s | T3 | `PLAN-observability-tuning.md` SUPERSEDED banner |
| — | merge | 07:25:41 | +9m | T3 | PR draft → ready → squash-merge → branch prune |

**Median slice latency: 8 min. Variance: 4 min (Slice 3, mechanical tailer) → 28 min (Slice 4, derived store + 21 vitest).** Slice 2's 13-min window includes the 36-second GPU draft, two strict-mode patches, the Python-golden fixture cross-check, and a `npm run test:run` pass.

## What the cascade did (and didn't)

### The 1 real GPU dispatch — Slice 2's TS port

`tool=generate ok=true latency_ms=36056`. The prompt opens *"Port the following Python function to idiomatic TypeScript. Output ONLY one fenced ts code block — no prose, no explanations, no test code. REQUIREMENTS: Bytes-native: input is Uint8Array, NOT a string. Never decode/re-encode the whole buffer..."* — a precise, constraint-front-loaded instruction with the Python source attached. The 4065-character draft Tier-3 received needed two strict-mode TypeScript fixes (per the PR's cascade-discipline note); Tier-3 also wrote the Python-golden fixture cross-check that proves byte-for-byte parity.

**Read:** this is the canonical "cheapest sufficient" win — Tier-2 produces the bulk of an algorithmic body that has a published canonical form, Tier-3 spends its attention on integration + verification + the cross-check that catches future port drift. The Slice-2 GPU dispatch is the template the protocol was designed around.

### The 11 NPU calls — all degraded

Every NPU record carries the same `RuntimeError: No OpenVINO device could load the Tier-1 model` payload. Pattern:

- **Record 1** (06:15:11 PT): the real one. `route()` probe for the Slice-2 TS port, fired 67 seconds before the GPU dispatch — Tier-3 climbed past the failed NPU on the documented escalation path.
- **Records 2–11** (06:35:01 → 07:10:06 PT, paired `route()`+`draft()`, ~8-10 min apart): background **cascade health probe** with a hardcoded "Write a Python function def dijkstra(graph, start)..." prompt. Every pair returned `available:false`. The same prompt appears in `runs/edge-npu.rec`'s 10 records and matches no actual dashboard task — almost certainly the wrapper's or a pre-push hook's diagnostic ping.

**The observability gap:** at the MCP call layer all 11 calls were `ok:true`. The "I'm not available" information was inside the `result` payload (`{"available": false, "reason": "..."}`). A cascade health dashboard reading only the top-level `ok` field would conclude the NPU was working all build. This is a real, generalizable miss — surfaced now precisely because we built a dashboard that's supposed to see exactly this.

### Zero `edge-verify` calls

Consistent with `projects/vinyl/CLAUDE.md`'s rule: **Vinyl/TS work bypasses the Python verifier** (which is Python-only and would mis-report). The gate is `npm run typecheck && lint && test:run`. The dashboard inherited this discipline; the `verify` MCP was wired into the session but never called.

## Notable events (non-cascade)

### 1. Grounding confusion at session start

The fresh edge-cli session, opening cold in the new `edge-cascade-dashboard` worktree, first read **`PLAN-observability-tuning.md`** (months-stale; lists `C1`/`A7`/`A8`/`P3` items) and surfaced its codes as the active backlog menu. The doc's name + repo-root position made it the natural attractive nuisance. Redirected via the cross-session memory pointer in the kickoff prompt; **cleanup committed as `219fd29` (SUPERSEDED banner + pointer to the live backlog).** Banner chosen over file-move so `git blame` on the historical content stays intact.

**General lesson:** any stale `PLAN-*.md` / `BACKLOG.md` / `TODO.md` at a repo root will dominate cold sessions until explicitly retired. Worth a one-pass sweep on any older repo.

### 2. Network outage mid-build (~30 min)

`github.com` TCP-connect failed across both sessions (DNS resolved fast at 0.02s; `connect_time=0`, curl exit 28 → connection block, not name resolution). `gh` keyring token *separately* stale (unrelated). Strategy held: slices stacked locally on `feat/dashboard` (`012f7d6 → e0671c8`); the cross-session memory bridge served as the review surface during the outage; once net recovered (~06:24 PT) the bundle pushed clean with upstream tracking.

**The commit-locally / push-when-reachable discipline made the outage a non-event.**

### 3. Tier-3 reviewer process crash mid-edit

The Tier-3 reviewer session (this one) hit a `Tool permission stream closed before response received` during an `Edit` on the cross-session memory file. The edit retried successfully on the next message after harness recovery. **No effect on the edge-cli session** (independent OS process). The slice-ledger-in-memory pattern survived because both sessions are independent readers/appenders of the same file — a crash in one doesn't block the other.

### 4. Inode tracking gap caught at review, folded forward

Slice 3 review (by Tier-3 reviewer) flagged that `tailer.ts` lacked the `inode` field the plan called for (`(path, ino, nextOffset)`). The gap was benign in practice — `cascade/logfmt.py`'s docstring is explicit that `.rec` is append-only — but the plan named it. **Folded into Slice 7 (+17 LOC):** `inode: number | null` added to `FileState`, `stat.ino` comparison on each tick, reset on mismatch, plus a vitest case simulating logrotate-style unlink+recreate. **Plan-conformance restored before Phase A closed.** The "fold the carry-forward into a later slice" pattern is reusable.

## Decision implications

- **The cascade tiers degrade *gracefully*.** NPU returning `available:false` cleanly is correct behavior — the protocol's failover engages, the work climbs to the next tier, no silent corruption.
- **There IS an observability gap.** `ok:true + available:false` is a cascade-health yellow that reads as green at the call layer. **PD-1 (degeneration guard) and a "cascade health" panel for the Phase B dashboard would close this** — the dashboard project itself is the natural vehicle to surface what `dashboard/src/store.ts::spend()` already does for cost: turn the badge yellow the instant any component reports `available:false`.
- **Slice-2-style algorithmic ports are GPU's sweet spot.** 36 seconds, 4065 chars, ~95% correct first try, two strict-mode patches. Cascade discipline aligned with CLAUDE.md as designed.
- **UI / wiring / debugging stayed Tier-3** — also per CLAUDE.md ("Best for: architecture, decomposition, integrating + reviewing"). The cascade was *not* under-used; the slice profile genuinely didn't have many bulk-algorithmic bodies.
- **The cross-session memory bridge worked through three shocks.** Slice ledger as an append-only review surface, plus the carry-forward block, plus periodic frontmatter updates by the reviewer session — this pattern is worth formalising for future multi-session work.

## Caveats / limits

- **Single run, environmental NPU outage.** N=1; the Tier-1 unavailability was an environmental accident, not a controlled probe.
- **No counterfactual baseline.** We don't know whether an all-Tier-3 build would have been faster/slower than this 1-GPU-dispatch run.
- **Token accounting not extracted.** The 1.96 MB transcript carries the real Tier-3 token usage; not parsed here.
- **NPU-unavailable is a particular failure mode.** Other Tier-1 degradations (slow drafts, wrong drafts that pass `ok` but fail the gate) would surface differently.
- **Period-probe inference is circumstantial.** The 10 NPU health-probes' source (which script/hook fires them, on what cadence rule) wasn't traced — only their content and timing. A `grep` for the dijkstra prompt in `scripts/` would identify the emitter.

## Reproduce / replay

- Build artifact at `main @ 19d2d6b` (PR #56).
- Slice lineage in reflog: `012f7d6 → 7ae4255 → e0671c8 → 51bb2ae → 2b75ee3 → 5ba17af → 1cf3b53 → 219fd29` (branch `feat/dashboard` deleted, commits orphaned but in the reflog).
- Cascade telemetry (gitignored): `runs/edge-gpu.rec`, `runs/edge-npu.rec` in `c:\Users\danth\src\edge-cascade-dashboard`.
- Inspect with `cascade/logfmt.py`:
  ```pwsh
  python -c "from cascade.logfmt import parse_stream; print(*parse_stream(open('runs/edge-npu.rec','rb').read()), sep='\n')"
  ```
- Edge-cli session transcript: `~/.claude/projects/C--Users-danth-src-edge-cascade-dashboard/2c6a5b58-*.jsonl`.

## What this unlocks

The retrospective surfaced **two first-order visibility deficiencies** — one at session-launch time, one at runtime. Both are I3·S2 and named below as the canonical fixes the build evidence motivates.

### SD-1 — edge-cli launch-time system summary (NEW, I3·S2)

`scripts/edge-cli.ps1` already wires the MCP servers; before it `exec`s into Claude Code, it should call each wired server's `.status` tool and print a one-line-per-tier summary, so any DEGRADED state is plain text at launch instead of buried in a `.rec` payload that only the retrospective surfaced. Sketch:

```
[edge-cli system summary at 2026-05-26 07:48 PT]
  cwd:        C:\Users\danth\src\edge-cascade-dashboard
  branch:     feat/dashboard @ 1cf3b53
  cascade:
    edge-npu     [DEGRADED]  available:false — openvino_genai not installed
    edge-gpu     [READY]     qwen2.5-coder:14b via Ollama
    edge-verify  [READY]     Python sandbox up
    edge-cloud   [NOT WIRED] (-WithCloud to enable)
  --strict-mcp-config: ON
```

~50 LOC of PowerShell, no Claude-side changes. Lives in `scripts/edge-cli.ps1` (or a sibling `scripts/edge-summary.ps1` the launcher invokes pre-exec). Catches the failure mode that hid for ~95 minutes in this build.

### SD-2 — dashboard cascade-health panel (NEW, I3·S2, ⊂ Phase B)

The "ladder part" deficiency: the dashboard renders cost (`spend.clean`) as a first-class signal but ignores tier availability — exactly the gap that let an NPU outage hide in plain sight for the entire Phase A build. Closed by extending `dashboard/src/store.ts` with a `health()` derived state mirroring `spend()`'s shape:

```ts
health(): { tiers: Record<Tier, "ready" | "degraded" | "unknown">; clean: boolean }
```

Derived from the most-recent `status` record per tier (the cascade already emits these on every health probe — we saw 11 of them in this run). `clean = all tiers === "ready"`. Add a `cascadeHealthRegion` next to the existing `rateMeterRegion`, subscribed to the same `TICK` key, with a yellow-flip on any non-ready tier. ~80 LOC + ~10 vitest. The dashboard becomes the runtime mirror of SD-1's launch-time check — **the dashboard project surfaces the gap the retrospective on it exposed.**

### Other items to file in the backlog

- **PD-1 (degeneration guard from the persona-debate findings)** gains scope: not just small-model degeneration, but **any MCP/tier `available:false` signal** treated as a first-class detector input. Refine the existing PD-1 entry rather than create a new one.
- **`mcp-degraded-state-audit` (NEW, I2·S2)** — all MCP consumers (`scripts/pr_review.py`, future `scripts/sdxl.py` for EI-1, anything calling `mcp__edge-*__*`) should treat `ok:true + available:false` as DEGRADED, not silent success. ~30 LOC across 2-3 files; could pair naturally with SD-2.
- **`cold-session-kickoff-guard` (NEW, I2·S2)** — add to `CLAUDE.md` (and/or the edge-cli launcher script's banner): "*ignore any `PLAN-*.md` / `BACKLOG.md` at the repo root unless it explicitly references the auto-memory `MEMORY.md` as canonical.*" The SUPERSEDED banner closes one case; this closes the pattern.
- **`audit-stale-planning-docs` (NEW, I2·S1)** — a one-pass `grep -ril "STALE\|SUPERSEDED\|FROZEN" docs/` + sweep of root-level `PLAN-*.md` / `DESIGN-*.md` to retire any other attractive nuisances before they bite the next cold session.
- **`document-cross-session-memory-pattern` (NEW, I2·S1)** — the slice-ledger-as-cross-session-bridge survived three independent shocks (Tier-1 outage, github TCP block, Tier-3 reviewer crash) and worked as designed. Capture the convention: append-only entries with `commit-sha + one-liner + carry-forward gap`, plus the carry-forward-fold-into-later-slice idiom. Lives in `docs/PATTERNS.md` or a CLAUDE.md section.
- **`NPU-reinstall` (NEW, I3·S1)** — `RuntimeError: No OpenVINO device could load the Tier-1 model: openvino_genai is required to run the NPU/iGPU tier. Install it: u[...]`. The hint was truncated; full command recoverable from the jsonl transcript or by re-running `edge-npu.status`. **Sequenced LAST** — the cascade degrades gracefully without Tier-1, and landing SD-1 + SD-2 first means the next outage of this kind will be impossible to miss.

### Phase B baseline

Phase B (eye-candy polish) gets this retrospective as its empirical baseline — median slice time 8 min, dispatch-to-GPU only for bulk-algorithmic ports, integration work stays Tier-3, gates pass on first try when the slice is well-scoped. SD-2 sits at the top of Phase B's queue.
