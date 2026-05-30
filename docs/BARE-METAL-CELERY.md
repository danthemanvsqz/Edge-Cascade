# Bare-metal Celery workers (Phase 2 Slice 5)

> **Status:** operational runbook. Companion to
> [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md) (the "workers pin to
> hardware" payoff) and [DESIGN-celery-phase2.md](DESIGN-celery-phase2.md)
> (Slice 5). No automated tests — the real test is a live cross-box bring-up.

The Canvas substrate makes every tier op a Celery task on its own queue, so the
workers can run **directly on the hardware that tier needs** instead of all on
one box. The broker (Redis) stays containerized — it's the message bus, not a
tier.

## Layout

```
        ┌──────────────┐         redis (broker + result backend)
        │  BROKER box  │  ◀── docker compose up -d redis  (containerized)
        │  redis:6379  │
        └──────┬───────┘
               │ CASCADE_REDIS_HOST=<broker ip>
       ┌───────┴────────────────────────────┐
       ▼                                     ▼
┌───────────────┐                    ┌──────────────────┐
│  INTEL box    │  -Q npu            │  RTX box         │  -Q gpu,verify
│  NPU drafter  │                    │  GPU coder       │
│  (OpenVINO)   │                    │  + verify gate   │
└───────────────┘                    └──────────────────┘

        cloud queue → NO worker (structural spend invariant)
```

Queue → box assignment (the contract the launch scripts honor):

| Queue    | Tier op(s)                         | Runs on        |
|----------|------------------------------------|----------------|
| `npu`    | `route`, `draft`                   | Intel box      |
| `gpu`    | `generate` (per-model), `model.swap` | RTX box      |
| `verify` | `verify_functional` (sandboxed exec) | either — defaults to RTX |
| `cloud`  | `cloud_generate`                   | **nobody** (see below) |

`verify` needs no accelerator (it just execs candidate code against the DSL), so
it can ride with either worker. It defaults to the RTX worker; **at least one**
worker must cover it or `balanced` chains stall at the gate step (more than one
is fine — the tasks just round-robin).

## 1. Broker box — Redis, reachable + authenticated

Single-box dev needs nothing beyond `docker compose up -d redis` (localhost).
**Cross-box, the broker is reachable off-host, so it MUST require a password.**

```powershell
docker compose up -d redis        # publishes 6379 on all host interfaces
```

The bundled [docker-compose.yml](../docker-compose.yml) runs redis with no auth
(fine for localhost-only). For cross-box, add a password and open the port:

- **Auth:** append `--requirepass <strong-secret>` to the redis `command:` (or
  run a separate hardened redis). Then every worker sets
  `CASCADE_REDIS_PASSWORD` (the scripts forward it).
- **Reachability:** Docker already publishes `6379:6379` on `0.0.0.0`, so the
  port is reachable once the **Windows firewall** allows inbound 6379 from the
  worker subnet. Scope the rule to the LAN — never expose 6379 to the internet,
  even with a password.

> Data stays ephemeral (`--save "" --appendonly no`): the broker is a bus, a
> restart comes up clean. `restart: unless-stopped` keeps it up across reboots.

## 2. Intel box — NPU drafter (`npu`)

```powershell
# RedisHost = the broker box; RedisPassword iff requirepass is set.
powershell -ExecutionPolicy Bypass -File scripts\start-worker-intel.ps1 `
    -RedisHost 10.0.0.5 -RedisPassword s3cret
```

Ensures the `accel` (OpenVINO) extra, resolves `CASCADE_NPU_MODEL_DIR` to the
main-tree 1.5B export, and starts a resident worker on the `npu` queue.

## 3. RTX box — GPU coder + verify gate (`gpu,verify`)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-worker-rtx.ps1 `
    -RedisHost 10.0.0.5 -RedisPassword s3cret
```

Defaults to the Ollama-over-HTTP backend (Ollama must be running on this box).
For the llama-cpp direct-load backend set `CASCADE_GPU_BACKEND=llama_cpp` and
launch with `-SyncExtras celery,llama_cpp`.

## 4. Verify it's wired

From any box with the venv (broker env set the same way):

```powershell
$env:CASCADE_REDIS_HOST = '10.0.0.5'; $env:CASCADE_REDIS_PASSWORD = 's3cret'
.\.venv\Scripts\python.exe -m celery -A cascade.celery_app inspect ping
# -> one pong per live worker (intel-npu@<host>, rtx-gpu@<host>)
```

Then dispatch a real solve and watch the `.rec` streams grow on each box:

```powershell
.\.venv\Scripts\python.exe scripts\mesh_solve_canvas.py --topology balanced "reverse a string"
```

## Windows gotchas (encoded in the scripts — here for the why)

- **`celery.exe` is blocked by WDAC App Control** on the maintainer's host
  (os error 4551, same family as the pre-commit hook block). Always launch via
  **`python -m celery`**, never the console script. The scripts do this.
- **The prefork pool is unreliable on Windows** → the scripts pass
  **`--pool=solo`** (proven in
  [FINDINGS-canvas-repair-retry-spike.md](FINDINGS-canvas-repair-retry-spike.md)).
  solo = one task at a time, which is what NPU/GPU want anyway
  (`worker_prefetch_multiplier=1`).

## Spend invariant (why no `cloud` worker)

The paid Tier-4 task (`cloud_generate_task`) is annotated `queue="cloud"`. If
**no worker subscribes `cloud`**, a dispatched cloud task enqueues but never
runs — structurally unspendable, the same guarantee edge-cli gets from
`--strict-mcp-config`. The launch scripts **refuse to start** if `cloud` is in
`-Queues`. To actually enable paid escalation you must consciously start a
worker on `cloud` AND have `CASCADE_ENABLE_CLOUD=1` + a key (config layer).

> **Always pass `-Q`.** If you invoke `python -m celery … worker` by hand
> *without* `-Q`, Celery consumes **every declared queue including `cloud`** —
> defeating the invariant. The launch scripts always pass `-Q`; bypass them and
> you own this.

## `.rec` aggregation caveat (multi-box)

Each task writes its tier's `runs/<tier>.rec` **on the box it ran on**. Single
box → one shared `runs/`, and `replay.py` / `dashboard.py` work unchanged.
**Multi-box splits the telemetry across hosts.** Until the aggregation collector
from [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md) (decision #1) exists,
the dashboard on any one box sees only that box's hops. Interim options: a
shared/SMB `runs/`, or copy each box's `.rec` to the dashboard host before
replay. Tracked as a Phase-3 item, not built here.

## Supervision / auto-restart

The scripts run a worker in the foreground (Ctrl-C to stop). For an
always-on box wrap the launch in a supervisor that restarts on exit — but
**not** a Windows Service via `celery.exe` (WDAC blocks it). A
`python -m celery` invocation under NSSM, a Scheduled Task ("run whether logged
on or not", restart-on-failure), or a simple `while ($true) { …; Start-Sleep 5 }`
relaunch loop all work. Resident warm state (NPU compile, Ollama load) survives
within a worker's lifetime via `worker_max_tasks_per_child=0`; a restart pays
the warm-up again.
