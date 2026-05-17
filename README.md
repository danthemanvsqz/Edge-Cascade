# edge-cascade

A multi-accelerator inference cascade for an Intel Core Ultra + NVIDIA laptop.
It routes coding prompts across **Intel NPU → NVIDIA GPU → Claude cloud**,
gating every hop on an objective code verifier and only spending money when
the free local tiers provably can't deliver.

## Hardware tiers

| Tier | Device | Backend | Model |
|------|--------|---------|-------|
| 1 | Intel NPU (AI Boost), iGPU fallback | OpenVINO GenAI | `qwen2.5-coder-1.5b` (sym INT4) |
| 2 | NVIDIA RTX 5070 Ti | Ollama | `qwen2.5-coder:14b` |
| 3 | Cloud (**paid, off by default**) | Anthropic API | `claude-sonnet-4-6` |

> The NPU only runs models exported with the NPU recipe
> (`--weight-format int4 --sym --ratio 1.0 --group-size=-1`). The stock
> `*-int4-ov` exports crash the vpux compiler; the probe falls back to the iGPU.

## What's in here

- **`cli.py`** — the 3-tier cascade: NPU router/draft → GPU → cloud, verifier-gated, with a live tee log (`runs/cascade.log`).
- **`lookahead.py`** — request-level speculative look-ahead: the NPU answers, earns a *trust window* by agreeing with the GPU, then runs solo; verifier-gated cloud escalation behind a **credit guard**.
- **`validate_log.py`** — extracts code from logs and validates it with a tiny **DSL** (`checks.dsl`); `--repair` feeds failures back to a model (`--repair-tier gpu|npu`) via the structured protocol in `cascade/feedback.py`.
- **`vs.py` / `webchat.py`** — NPU-vs-GPU side-by-side, in the terminal or a local web page.

## Setup (uv)

```bash
uv sync                 # core deps + dev tools (fast; no ML stack)
uv sync --extra accel   # add the Intel NPU/iGPU stack (OpenVINO GenAI) — large
```

The cloud tier needs `ANTHROPIC_API_KEY`. Put it in a local `.env`
(git-ignored — see below). It stays **off** unless you pass `--cloud` /
`enable_cloud=True` / `CASCADE_ENABLE_CLOUD=1`, and is further bounded by the
credit guard (`CASCADE_CLOUD_MAX_CALLS`, default 3; `CASCADE_CLOUD_USD`,
default 0.50, per run).

## Run

```bash
uv run python cli.py "write a binary search in python"
uv run python lookahead.py            # built-in task stream
uv run python validate_log.py --repair
uv run python vs.py                   # terminal side-by-side
```

## Secrets

**Never commit secrets.** `.env` and `*.key` are in `.gitignore`; the code
reads `ANTHROPIC_API_KEY` only from the environment / `.env`, never source.
If a key is ever pasted or leaked, rotate it at
<https://console.anthropic.com/settings/keys>.

## Tests & coverage policy

```bash
uv run pytest
```

The suite enforces **`fail_under = 100`** — but *scoped*, not project-wide.
100% is measured over the pure, safety-critical logic:

- `cascade/config.py` — env/config + the cloud gate
- `cascade/feedback.py` — the repair protocol
- `cascade/verifier.py` — the escalation gate
- `cascade/cloud_worker.py` — credit-guard cost math + cloud gating

Excluded from the gate (see `pyproject.toml [tool.coverage.run] omit`):
`npu_worker`, `gpu_worker`, `orchestrator`, `lookahead`, and the CLI/server
entrypoints. These require real NPU hardware, a running Ollama, the paid API,
or a `__main__`/HTTP loop. Mock-theater tests for them would assert against
fakes, adding maintenance risk without real assurance. They're exercised by
the live smoke runs instead. Tightening this (with hardware fakes) is a
deliberate future choice, not an accident — hence the explicit `omit`.
