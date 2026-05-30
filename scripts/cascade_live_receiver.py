"""Live cascade-activity event-receiver runner (the dashboard's push producer).

Per docs/DESIGN-observability-lanes.md (D2): a Celery `app.events.Receiver` --
a consumer, not a task, so no solo-worker trap -- watches task transitions, maps
the currently-STARTED tasks to chain nodes (cascade.live_receiver.nodes_for),
diffs against the last snapshot (node_delta), and publishes each `{node, state}`
delta to a Redis pub/sub channel. The Vinyl dashboard subscribes (push); the UI
never polls.

Run it alongside the Canvas worker (needs worker_send_task_events=True, already
set in cascade/celery_app.py):

    uv run python scripts/cascade_live_receiver.py

Live substrate: the pure projection it drives (nodes_for / node_delta) is
gate-covered in cascade/live_receiver.py; this runner is the broker/redis glue,
live-validated like the worker, not unit-cov'd.
"""
from __future__ import annotations

import json

import redis

from cascade.celery_app import app
from cascade.live_receiver import LIVE_CHANNEL, node_delta, nodes_for


def run(channel: str = LIVE_CHANNEL) -> None:
    pub = redis.Redis.from_url(app.conf.broker_url)
    state = app.events.State()
    prev: set[str] = set()

    def on_event(event: dict) -> None:
        nonlocal prev
        state.event(event)
        # The STARTED tasks are the ones actually spinning right now (task_track_
        # started makes the event fire at execution, not enqueue).
        active = [t.name for t in state.tasks.values() if t.name and t.state == "STARTED"]
        curr = nodes_for(active)
        for node, node_state in node_delta(prev, curr):
            pub.publish(channel, json.dumps({"node": node, "state": node_state}))
        prev = curr

    with app.connection() as conn:
        receiver = app.events.Receiver(conn, handlers={"*": on_event})
        print(f"[live-receiver] capturing task events -> redis pub/sub '{channel}'")
        receiver.capture(limit=None, timeout=None, wakeup=True)


if __name__ == "__main__":
    run()
