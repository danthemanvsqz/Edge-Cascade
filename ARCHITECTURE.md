# Edge Cascade — Architecture

> **⚠️ SUPERSEDED FRAMING (2026-05-30).** This document specifies the original
> **MCP topology** — the agent as MCP *client* hand-orchestrating one server per
> tier (`edge-npu`/`edge-gpu`/`edge-verify`/`edge-cloud`). That per-tier
> hand-driven path is **retired for inference**. The cascade is now defined ONCE
> in code as `cascade.mesh.solve`, dispatched as a **Celery Canvas pipeline**
> (NPU-first → draft → gate → bounded GPU repair → win/lose logger last) and
> invoked as a single blocking call via `cascade.canvas_client.solve_*_canvas`
> / `scripts/mesh_solve_canvas.py`. **The current architecture is
> `docs/DESIGN-celery-canvas.md`; the agent contract is `CLAUDE.md` + the
> `/edge-cascade` skill.** The hardware/trust-boundary mapping and tier
> rationale below remain accurate as background; the *transport* (MCP request
> dispatch) and the *client = orchestrator* inversion no longer describe how
> inference is routed.

A local-first, heterogeneous LLM inference mesh. Cheap local accelerators do the
volume; a quota-windowed Claude subscription agent does the orchestration and the
hard reasoning; a credit-guarded paid API is the autonomous backstop.

This document specifies the **MCP topology** and how each server maps to a
hardware/trust boundary, and points to the **skill** that is the user interface.

---

## 1. The central inversion

The original `cli.py` is a monolith: one query in, one answer out, with the
Python `Orchestrator` doing all routing internally. That makes the cascade a
black box and wastes the fact that the agent driving it (Claude Code, Opus 4.7,
on a Pro/Max subscription) *is itself the intelligence ceiling*.

This architecture inverts control:

- **Tier 3 is the agent, and the agent is the MCP _client_.** It is not a
  server, not a process in the pipeline, not an API call. It is the Central
  Architecture Router from `CLAUDE.md`, composing the cascade itself.
- **Each lower tier is an MCP _server_ exposing one hardware boundary** as a set
  of discrete tools the agent calls and composes — instead of delegating routing
  to a fixed Python pipeline.

The payoff: routing, chunking, verification, and escalation are decisions the
*router* makes per sub-task (what `CLAUDE.md` already mandates), not behaviour
buried in `orchestrator.py`.

---

## 2. Topology

```
                 ┌──────────────────────────────────────────────┐
                 │  TIER 3 — Claude Code agent (Opus 4.7)        │
                 │  = MCP CLIENT / Central Architecture Router    │
                 │  subscription · quota-windowed · unmetered     │
                 └──┬──────────┬───────────┬───────────┬─────────┘
            stdio   │   stdio  │    stdio  │    stdio   │
          ┌─────────▼─┐ ┌──────▼─────┐ ┌───▼────────┐ ┌─▼───────────────┐
          │ edge-npu  │ │  edge-gpu  │ │ edge-verify│ │   edge-cloud     │
          │ MCP server│ │ MCP server │ │ MCP server │ │   MCP server     │
          ├───────────┤ ├────────────┤ ├────────────┤ ├──────────────────┤
          │ TIER 1    │ │ TIER 2     │ │ det. gate  │ │ TIER 4           │
          │ Intel NPU │ │ NVIDIA RTX │ │ NO model   │ │ Anthropic API    │
          │ /iGPU/CPU │ │ 5070 Ti    │ │ Intel CPU  │ │ Opus (paid)      │
          │ OpenVINO  │ │ Ollama     │ │ AST+DSL+   │ │ credit-guarded   │
          │ 1.5B INT4 │ │ 14B Q4     │ │ sandbox    │ │ clean context    │
          └───────────┘ └────────────┘ └────────────┘ └──────────────────┘
           local stdio   local stdio    local stdio    HTTPS — the ONLY
           (no network)  →:11434        (subproc sbx)  hop off this machine
```

**The transport boundary == the cost boundary == the trust boundary.** The three
local servers speak stdio and never touch the network or your wallet.
`edge-cloud` is the only server that crosses the machine boundary, the only paid
one, and the only one whose output the agent treats as already-trusted (it skips
the local gate). One clean line separates "free, local, must-verify" from "paid,
remote, authoritative".

---

## 3. MCP servers → hardware

### 3.1 `edge-npu` — Tier 1 (Intel NPU "AI Boost" / Xe iGPU / CPU)

Engine: OpenVINO GenAI `LLMPipeline`, `qwen2.5-coder-1.5B` channel-wise
**symmetric INT4** (the only layout the vpux compiler accepts —
see `edge-inference-setup` memory).

**OpenVINO runs in an isolated worker subprocess, never in this server's
process** (`mcp_servers/_npu_worker_proc.py`, private pipe). Measured root
cause: `openvino_genai.LLMPipeline` compile returns in ~3.5 s in any normal
process — and even with the bare MCP stdio transport up — but **never returns
when run inside the full FastMCP request-dispatch path**, on any thread
(process-global; not stdout corruption, not slowness, not the event-loop
thread, all disproven by probe). The worker process has none of that
machinery, so the compile completes normally; the server is a thin, bounded
RPC client that never imports OpenVINO. The worker reserves fd 1 for its JSON
protocol and routes all native/`print()` output to stderr.

| Tool | Signature | Purpose |
|------|-----------|---------|
| `route` | `(prompt) -> {difficulty: float, category: str, latency_s}` | The cheap up-front classifier. Drives all routing. |
| `draft` | `(prompt) -> {text, device, latency_s}` | Fast boilerplate / trivial-code generation. |

Resource: `npu://status` → which device actually compiled (`NPU` → `GPU.0` →
`CPU` fallback order) and the static-shape prompt cap.

**Hardware-owned concerns the server hides from the agent:**
- The vpux compiler can **hard-abort (exit 127, uncatchable)** on a wrong quant
  layout. The server runs the NPU probe in an isolated subprocess and silently
  falls back to iGPU; the agent only ever sees a working device string.
- Static-shape limits (`npu_max_new_tokens=192`, repair `640`). Exposed as tool
  metadata so the router never hands Tier 1 an input it structurally can't take.

### 3.2 `edge-gpu` — Tier 2 (NVIDIA RTX 5070 Ti Laptop, 12 GB)

Engine: Ollama at `localhost:11434`, `qwen2.5-coder:14b` Q4 (~9 GB).

| Tool | Signature | Purpose |
|------|-----------|---------|
| `generate` | `(prompt, prior_attempt?) -> {text, tokens_per_s, latency_s, model}` | Local reasoning, multi-file logic, repair of a failed Tier-1 draft. |

Resource: `gpu://status` → Ollama reachable? model resident? **VRAM headroom.**

**Reality check baked into the server:** 12 GB (not the 16 GB the design papers
assume). 14B-Q4 leaves only ~2–3 GB for KV cache → realistic local context is
~8–32K, not 128K. `gpu://status` reports the live context ceiling so the router
escalates big-context tasks instead of letting them OOM. If Ollama is down the
tool returns `available: false` (not an error) so the router can route around it.

### 3.3 `edge-verify` — deterministic gate (Intel CPU, **no model**)

This is Pillar 3 of the research doc made concrete: a non-LLM, 100%-reproducible
gate that every local answer must pass before the agent trusts it. It breaks the
"self-correction blind spot" — the agent never grades its own (or a local
model's) output; the compiler does.

| Tool | Signature | Purpose |
|------|-----------|---------|
| `verify_syntax` | `(text) -> {passed, has_code, reason}` | Fast gate: extract fenced block, `compile()` (AST only — **never `exec`**). From `verifier.py`. |
| `verify_functional` | `(text, dsl?) -> {passed, failures:[{expr,observed,requirement}]}` | Runs `checks.dsl` assertions against the candidate in an **isolated subprocess sandbox** (timeout-bounded; Docker/WASM-upgradeable). |
| `repair_prompt` | `(task, code, failures) -> str` | Formats the model-legible fix request from `feedback.py`. |

Runs purely on CPU — no NPU/GPU contention, so it stays fast while the
accelerators are busy. The sandbox is mandatory: model output is hostile input
and is never evaluated in-process.

### 3.4 `edge-cloud` — Tier 4 (Anthropic API, Opus, paid)

The autonomous backstop. Used when the agent is **absent/throttled**, or to
break a genuine deadlock with a clean context window. Wraps `cloud_worker.py`.

| Tool | Signature | Purpose |
|------|-----------|---------|
| `escalate` | `(query, prior_attempt?, verifier_reason?, mode="repair"\|"critic") -> {text, model, est_cost_usd, in_tok, out_tok}` | Paid escalation. `critic` = fresh-context second opinion (deadlock breaker). |
| `budget` | `() -> {calls_used, calls_max, usd_spent, usd_budget, allowed}` | Credit-guard state. The router must check this before `escalate`. |

**Budget bug fixed here (was latent):** the cost estimate in `cloud_worker.py`
uses a **per-model price table** (`_price_for`, keyed on `cloud_model` by id
prefix): Opus is billed `$15/$75` per MTok, Sonnet `$3/$15`, and an unknown model
is costed at the dearest known rate so a new model can never under-count. This
replaced the old hardcoded Sonnet rates, which would have made the guard
under-count Opus spend ~5× and silently blow `cloud_usd_budget`. Model id, price,
and budget move together. `budget()` is the guard the router consults; it refuses
once `cloud_max_calls` or `cloud_usd_budget` is reached.

---

## 4. Why the agent is not a server

Tier 3 (me) deliberately has **no MCP surface**:

- It is the *client* that composes the other four. A server-of-itself would
  re-introduce the monolith.
- Its "cost model" is the subscription quota window, which cannot be exposed as
  a metered tool (that is exactly Tier 4's job).
- It only exists *in the loop*. The MCP servers are indifferent to who the
  client is — which is what makes the two run modes (§5) fall out for free.

---

## 5. Two run modes, one set of servers

| | Interactive (agent present) | Unattended (agent throttled / batch) |
|--|------------------------------|--------------------------------------|
| MCP **client** | Claude Code agent | thin headless runner (`orchestrator.py` adapted) |
| Tier 3 | the agent reasons directly | collapses out |
| Ceiling | agent; `edge-cloud` only on deadlock | `edge-cloud` (Opus), credit-guarded |
| Cost | unmetered (quota window) | metered, capped by `budget()` |

Because the servers don't care who calls them, "finish work while the agent is
throttled" is just: launch the work as an unattended-mode run that already
carries the `edge-cloud` backstop (kicked off before throttling, or scheduled).

---

## 6. Data flow — one task

1. Router emits a `routing_dispatch` block (per `CLAUDE.md`).
2. `edge-npu.route(task)` → `{difficulty, category}`.
3. Branch on the thresholds in `config.py`:
   - `< 0.70` → `edge-npu.draft` → **`edge-verify.verify_syntax`**.
   - `0.70–0.80` → `edge-gpu.generate` → `verify_syntax` (+ `verify_functional`
     if a DSL block applies).
   - `≥ 0.80` → skip local; the **agent answers directly** (Tier 3).
4. On a failed gate: `edge-verify.repair_prompt(...)` → retry one tier up,
   passing the failed draft as `prior_attempt`.
5. **Deadlock check:** if two of the agent's own retries are
   `SequenceMatcher ≥ 0.90` (consensus inertia), check `edge-cloud.budget()`;
   if allowed, `edge-cloud.escalate(mode="critic")` for a clean-context opinion.
6. The agent verifies the final answer before returning it.

---

## 7. Wiring (`.mcp.json`) — spec, not yet live

```jsonc
{
  "mcpServers": {
    "edge-npu":    { "command": ".venv/Scripts/python", "args": ["-m", "mcp_servers.npu"] },
    "edge-gpu":    { "command": ".venv/Scripts/python", "args": ["-m", "mcp_servers.gpu"] },
    "edge-verify": { "command": ".venv/Scripts/python", "args": ["-m", "mcp_servers.verify"] },
    "edge-cloud":  { "command": ".venv/Scripts/python", "args": ["-m", "mcp_servers.cloud"],
                     "env": { "CASCADE_ENABLE_CLOUD": "1" } }
  }
}
```

All four use stdio transport; `edge-cloud` additionally reads
`ANTHROPIC_API_KEY` from the environment / `.env`.

---

## 8. Scope boundary

This document is **architecture + interface design**. Implementation — the four
`mcp_servers/*` modules, the per-model price table, the live `.mcp.json`, and
the sandbox upgrade for `verify_functional` — is the build step and is
intentionally out of scope here. The user interface is the skill:
`.claude/skills/edge-cascade/SKILL.md`.
