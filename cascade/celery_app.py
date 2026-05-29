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

from celery import Celery

# Redis for everything. One URL, two roles (broker + backend).
REDIS_URL = os.environ.get("CASCADE_REDIS_URL", "redis://localhost:6379/0")

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
