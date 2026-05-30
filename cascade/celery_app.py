"""Celery app for the mesh substrate (C1 Phase-0 spike) -- OPT-IN.

Redis is BOTH broker and result backend (decision 2026-05-22: one service for
the spike; revisit RabbitMQ only at the durability/multi-box decision gate --
see docs/DESIGN-celery-canvas.md). Each tier op becomes a Celery task on its own
queue, so workers can later pin to hardware (npu queue -> Intel box, gpu queue
-> RTX box). Nothing in the pipe/in-process hot path imports this.

Run (after `docker compose up -d redis` and `uv sync --extra celery`):
    uv run celery -A cascade.celery_app worker -Q npu,gpu,verify -l info
"""
from __future__ import annotations

import os
from urllib.parse import quote

from celery import Celery


# Redis for everything. One URL, two roles (broker + backend).
def _redis_url() -> str:
    """Broker+backend URL. Full override wins; else assemble from parts.

    Single-box dev needs nothing -- defaults to localhost. Cross-box bare-metal
    (Phase 2 Slice 5) points each worker at the broker box WITHOUT hand-writing
    a URL on every host:
        $env:CASCADE_REDIS_HOST = "10.0.0.5"     # the box running redis
        $env:CASCADE_REDIS_PASSWORD = "..."       # iff redis has `requirepass`
    `CASCADE_REDIS_URL` still wins when set, for layouts the parts can't express
    (a non-zero db, rediss:// TLS, a unix socket). See docs/BARE-METAL-CELERY.md.
    """
    if url := os.environ.get("CASCADE_REDIS_URL"):
        return url
    host = os.environ.get("CASCADE_REDIS_HOST", "localhost")
    port = os.environ.get("CASCADE_REDIS_PORT", "6379")
    password = os.environ.get("CASCADE_REDIS_PASSWORD", "")
    # URL-encode the secret: a requirepass value may contain @ : / # %, which
    # would otherwise corrupt the URL and silently connect to the wrong host or
    # fail auth (the exact `requirepass <strong-secret>` flow the runbook documents).
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


REDIS_URL = _redis_url()

app = Celery(
    "edge_cascade",
    broker=REDIS_URL,
    backend=REDIS_URL,
    # The worker needs every module that registers @app.task fns: the tier op
    # wrappers (`cascade.tasks`), the bounded GPU repair task from the Phase-0
    # spike (`cascade.canvas_spike`), and the balanced-topology chain steps
    # (`cascade.topologies_canvas`). Missing any one of these means a chain
    # dispatch yields a `NotRegistered` error on `.get()` -- the eager test
    # suite doesn't catch this because eager mode skips the broker entirely
    # (Slice-4 live-broker discovery on 2026-05-28; see FINDINGS-canvas-phase1.md).
    include=[
        "cascade.tasks",
        "cascade.canvas_spike",
        "cascade.topologies_canvas",
    ],
)

app.conf.update(
    # JSON only -- task args/results cross the wire as plain data (charter
    # invariant 2: if it isn't serializable it can't be a task).
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    task_track_started=True,
    # Backpressure WITHOUT RabbitMQ: a worker holds at most one unacked task and
    # pulls the next only when free. acks_late = a task is re-queued if its
    # worker dies mid-run (LLM calls are long; we want at-least-once).
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Keep the worker RESIDENT: never recycle the child, or the ~12-21s NPU
    # compile (and Ollama warm state) is thrown away between tasks.
    worker_max_tasks_per_child=0,
    # The one Redis-broker footgun: a task running longer than visibility_timeout
    # gets redelivered (double-run). LLM `generate` can take ~180s, so set the
    # window well above the longest task. (RabbitMQ wouldn't need this.)
    broker_transport_options={"visibility_timeout": 3600},
)
