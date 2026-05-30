---
name: edge-image
description: >-
  Generate images with the local SDXL model server, with Claude as the prompt
  mediator AND the vision critic. Use when the user asks to generate / make /
  create / render an image or picture, or invokes /edge-image. You turn loose
  intent into a tuned SDXL spec, POST it to the local image_server, then READ the
  produced PNG with your own vision to judge it and iterate. Requires the server
  (scripts/image_server.py) running on CASCADE_IMAGE_URL (default :8188).
---

# edge-image — local SDXL, you are the mediator + critic

You drive a local Stable Diffusion XL server. There is **no separate critic
model**: *you* craft the prompt and *you* look at the result. Images are local
(SDXL on the RTX) — **spend stays $0**, never reach for a paid tier.

## 0. Preflight — is the server up?

```
curl -s http://localhost:8188/health
```
- `{"available": true, ...}` → go.
- Connection refused / `available:false` → tell the user to start it (it holds
  SDXL resident, ~30 s to load). The launch is one line if the env is already
  set up:
  ```powershell
  $env:CASCADE_FREE_OLLAMA = "1"
  .\.venv\Scripts\python.exe -m uvicorn scripts.image_server:app --port 8188
  ```
  `CASCADE_FREE_OLLAMA=1` evicts the 14B coder — **SDXL and the coder cannot
  share 12 GB VRAM**, so imaging and coding don't run at the same time (yet).

  If the env is NOT set up (`No module named 'fastapi'`, or `Torch not compiled
  with CUDA enabled` after startup), do the full setup from
  `pyproject.toml`'s `imagegen =` block comment — abbreviated:
  - `.venv` must be on **Python 3.13** (cp314 has no torch CUDA wheels).
  - Use **`uv sync --all-extras`** — never `uv sync --extra imagegen` alone
    (that one purges `mcp`/`openvino`/`pywin32` and breaks edge-cli + Tier 1).
  - Then install the CUDA torch wheel matching the GPU: **cu128** for RTX
    50-series (Blackwell, sm_120), **cu124** for older.

  Don't try to script the env fix yourself; hand the user the relevant
  command and let them run it (they own the host).

## 1. Mediate — intent → a tuned SDXL spec

Do NOT pass the user's words through raw. Build a structured spec:

- **prompt**: concrete and layered — subject, then style/medium, then
  composition, lighting, detail/quality cues. SDXL rewards specificity
  ("a red fox curled asleep on moss, golden hour, shallow depth of field,
  photoreal, 85mm" ≫ "a fox").
- **negative_prompt**: a strong default unless the user wants otherwise —
  `lowres, blurry, deformed, extra fingers, bad anatomy, watermark, text,
  jpeg artifacts`.
- **width/height**: 1024×1024 default; 1024×1280 portrait / 1280×1024 landscape
  (SDXL is trained at ~1MP — don't go below 768).
- **steps** 25–40 (default 30), **guidance_scale** 5–8 (default 6.5; lower =
  more creative, higher = more literal).
- **seed**: pick an explicit integer and report it, so a good result is
  reproducible and a tweak changes one thing at a time.

State the spec to the user before generating (one compact line).

## 2. Generate

```
curl -s -X POST http://localhost:8188/generate -H 'Content-Type: application/json' \
  -d '{"prompt":"...","negative_prompt":"...","width":1024,"height":1024,
       "steps":30,"guidance_scale":6.5,"seed":12345}'
```
Returns `{"path": "runs/artifacts/<ts>-<seed>.png", "seed":..., "latency_s":...}`.
The server already recorded it to `runs/edge-image.rec` (telemetry; $0).

## 3. Critique — opt-in, not default

**Default = skip to §5 Deliver.** Report the artifact path + spec and let the
user decide whether to look at the result themselves. The critique step is a
real Tier-3 turn that can halt on the output filter for affect-laden prompts
(see "Affect-heavy prompts" below); making it opt-in keeps the happy path
robust.

**Critique only when EITHER:**
- The user asks for feedback / iteration / "is it good?", or
- The user's original intent included a specific quality bar that needs
  verification (and even then, ask first: "want me to look at it before
  delivering?"), or
- They invoked the skill with an explicit critique request in the same turn.

If critiquing: **Read the PNG** (`Read(<path>)`) so you actually SEE it, then
judge it against the user's intent — composition, prompt adherence, obvious
artifacts (hands, faces, garbled text), framing. Be specific and honest; don't
claim it's good without looking.

### Affect-heavy prompts — prefer direct-drive

Prompts touching emotional themes (loss, catharsis, distress, grief, "dark
place", trauma, etc.) can halt the Tier-3 critique turn on the Anthropic API's
output filter — `400 Output blocked by content filtering policy` — even when
the actual narrative is uplifting. The filter scores model TEXT output, not
the image; the diagnosis is documented in
`docs/FINDINGS-edge-image-content-filter.md`. For these prompts:

- **Default to NO critique** (don't `Read` the PNG, don't write prose about it).
  Deliver the artifact path + spec and stop.
- **Or prefer `scripts/sdxl.py`** (EI-1, the direct-drive client shipped in
  PR #63): `uv run python scripts/sdxl.py --prompt "..."`. It POSTs the same
  spec to the same server but cuts Tier-3 out of the loop entirely — no
  mediation, no critique, no filter exposure.

The user can always come back and ask for a look at the result; the lifeline
is that the agent turn doesn't get killed mid-flow.

## 4. Iterate — opt-in, paired with critique

Iteration only runs when the critique step ran AND found something to fix. If
§3 was skipped (the default), §4 is skipped too — the user iterates themselves
by re-invoking the skill with adjusted intent. When iterating: change ONE lever
at a time and regenerate (keep the seed to test a prompt change; change the
seed to roll a new composition). **Cap at ~3 rounds** — then show the best
result and tell the user what you'd try next rather than looping forever.

## 5. Deliver

Report the final `runs/artifacts/…png` path, the exact spec used (so it's
reproducible), and a one-line note on what worked / what to tune. If §3 was
skipped, the "what worked" line is the spec rationale ("layered subject /
style / 30 steps / seed 12345 for reproducibility"), not a vision-derived
judgment.

## Boundaries
- Local only; **never** a paid tier — image-gen is $0.
- Don't pull/swap models or edit host config. If the server is down, ask the
  user to start it; don't try to load SDXL yourself.
- If the user is mid-coding, warn that starting the image server evicts the
  coder from VRAM (and vice-versa) until the Celery model-swap arbiter lands.
- Don't critique an affect-heavy result speculatively. The filter that halts
  the Tier-3 turn doesn't care about your good intent — see "Affect-heavy
  prompts" above.
