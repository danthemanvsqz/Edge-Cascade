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
  SDXL resident, ~30 s to load), then stop:
  ```
  uv sync --extra imagegen           # one-time (CUDA torch — see pyproject note)
  CASCADE_FREE_OLLAMA=1 uv run uvicorn scripts.image_server:app --port 8188
  ```
  `CASCADE_FREE_OLLAMA=1` evicts the 14B coder — **SDXL and the coder cannot
  share 12 GB VRAM**, so imaging and coding don't run at the same time (yet).

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

## 3. Critique — with your own vision

**Read the PNG** (`Read(<path>)`) so you actually SEE it, then judge it against
the user's intent — composition, prompt adherence, obvious artifacts (hands,
faces, garbled text), framing. Be specific and honest; don't claim it's good
without looking.

## 4. Iterate (bounded)

If it misses, change ONE lever at a time and regenerate (keep the seed to test a
prompt change; change the seed to roll a new composition). **Cap at ~3 rounds** —
then show the best result and tell the user what you'd try next rather than
looping forever.

## 5. Deliver

Report the final `runs/artifacts/…png` path, the exact spec used (so it's
reproducible), and a one-line note on what worked / what to tune.

## Boundaries
- Local only; **never** a paid tier — image-gen is $0.
- Don't pull/swap models or edit host config. If the server is down, ask the
  user to start it; don't try to load SDXL yourself.
- If the user is mid-coding, warn that starting the image server evicts the
  coder from VRAM (and vice-versa) until the Celery model-swap arbiter lands.
