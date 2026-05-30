"""Live cascade-activity event receiver -- the dashboard's push producer.

Per docs/DESIGN-observability-lanes.md (D2): the spinning-node signal is
event-driven (discrete task transitions), so it's driven by a Celery
`app.events.Receiver` -- a *consumer* (like Flower), not a task on a queue, so
it sidesteps the solo-worker trap and catches every transition incl. the
sub-100ms NPU steps a poll would skip. On each transition the receiver
recomputes the active node set and publishes the delta to a Redis pub/sub
channel; the Vinyl dashboard subscribes (push) and the UI never polls.

This module keeps the projection pure (`nodes_for`, `node_delta`) so the gate
covers it; the live `app.events.Receiver` loop + Redis publish are live
substrate (a real broker + worker), not unit-cov'd.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from cascade.flower_activity import NODE_BY_TASK

# Redis pub/sub channel the receiver publishes node-state deltas on; the Node
# dashboard subscribes. JSON frames: {"node": str, "state": "active"|"idle"}.
LIVE_CHANNEL = "cascade.live.nodes"

# Redis key holding the CURRENT active-node set (JSON sorted list). Pub/sub is
# fire-and-forget, so a dashboard that connects mid-solve would miss the deltas
# that already fired; it GETs this key on connect to seed, then rides the deltas.
LIVE_STATE_KEY = "cascade.live.active"


def nodes_for(names: Iterable[str]) -> set[str]:
    """Map active celery task names to the set of chain-node ids they occupy.

    Names not in `NODE_BY_TASK` (Flower's own tasks, celery internals) are
    dropped, exactly like `flower_activity.parse_active`.
    """
    out: set[str] = set()
    for name in names:
        mapped = NODE_BY_TASK.get(name)
        if mapped is not None:
            out.add(mapped[0])
    return out


def node_delta(prev: set[str], curr: set[str]) -> list[tuple[str, str]]:
    """Transitions between two active-node snapshots, sorted for determinism.

    A node in `curr` but not `prev` just became active; a node in `prev` but
    not `curr` went idle. The two sides are disjoint, so sorting the combined
    list orders it by node id.
    """
    transitions = [(node, "active") for node in curr - prev]
    transitions += [(node, "idle") for node in prev - curr]
    return sorted(transitions)


def publish_state(pub, channel: str, state_key: str, prev: set[str], curr: set[str]) -> set[str]:
    """Publish the transitions between two snapshots and update the seed key.

    `pub` is an injected redis client (`.publish` + `.set`). Each `node_delta`
    transition is published to `channel`; then the current active set is written
    to `state_key` as a JSON sorted list so a late-joining subscriber can seed.
    Returns `curr` so the caller can roll it into `prev`.
    """
    for node, node_state in node_delta(prev, curr):
        pub.publish(channel, json.dumps({"node": node, "state": node_state}))
    pub.set(state_key, json.dumps(sorted(curr)))
    return curr
