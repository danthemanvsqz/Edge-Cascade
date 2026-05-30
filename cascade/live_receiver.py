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
import time
from collections.abc import Iterable

from cascade.flower_activity import NODE_BY_TASK

# Minimum-lit window (BACKLOG #12): a fast node (route/draft is sub-second)
# would blink on/off faster than the eye catches. Hold its `idle` for at least
# this long after it went active, so every node visibly lights. Slow nodes
# (gpu_solve ~60s) far exceed it, so they're unaffected -- they just spin.
MIN_LIT_S = 0.6

# Redis pub/sub channel the receiver publishes node-state deltas on; the Node
# dashboard subscribes. JSON frames: {"node": str, "state": "active"|"idle"}.
# Contract mirror: dashboard/src/lib/liveSource.ts (rename both sides together).
LIVE_CHANNEL = "cascade.live.nodes"

# Redis key holding the CURRENT active-node set (JSON sorted list). Pub/sub is
# fire-and-forget, so a dashboard that connects mid-solve would miss the deltas
# that already fired; it GETs this key on connect to seed, then rides the deltas.
LIVE_STATE_KEY = "cascade.live.active"

# Topology graph channel: Celery Beat publishes the full {name, nodes, edges}
# graph on this channel every 30 s and on worker startup. The dashboard
# subscribes and calls setTopologyGraph() on each message so the SVG always
# reflects the live canvas without a server restart.
TOPOLOGY_CHANNEL = "cascade.live.topology"
TOPOLOGY_STATE_KEY = "cascade.live.topology.current"


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


def hold_remaining(active_since: float, now: float, min_lit: float) -> float:
    """Seconds a node must stay lit to satisfy the minimum-lit window, or 0.0 if
    it has already been lit long enough (BACKLOG #12). Never negative."""
    return max(0.0, min_lit - (now - active_since))


def publish_state(
    pub,
    channel: str,
    state_key: str,
    prev: set[str],
    curr: set[str],
    active_since: dict[str, float],
    now: float,
    min_lit: float = MIN_LIT_S,
    sleep=time.sleep,
) -> set[str]:
    """Publish the transitions between two snapshots and update the seed key.

    `pub` is an injected redis client (`.publish` + `.set`). Each `node_delta`
    transition is published to `channel`. An `idle` is HELD (`sleep`) until the
    node has been lit for `min_lit` (BACKLOG #12: keeps a fast route/draft node
    visible); a slow node is past that window, so it publishes immediately.
    `active_since` (caller-owned, mutated here) records each node's activation
    time and is popped on idle so it never grows. Then the current set is written
    to `state_key` so a late subscriber can seed. Returns `curr` to roll into
    `prev`. `sleep` is injectable for tests.

    NB: the hold calls `sleep` SYNCHRONOUSLY, so this BLOCKS the caller (the
    Celery event-receiver thread) for up to `min_lit` per idle. Fine for v1 --
    holds serialize into a clean sequential light-up -- but a burst of fast
    transitions delays later `active` publishes too. The v2 iteration is a
    non-blocking scheduler (a `not_before` per node, flushed on the next event).
    """
    for node, node_state in node_delta(prev, curr):
        if node_state == "active":
            active_since[node] = now
        else:
            wait = hold_remaining(active_since.pop(node, now), now, min_lit)
            if wait > 0:
                sleep(wait)
        pub.publish(channel, json.dumps({"node": node, "state": node_state}))
    pub.set(state_key, json.dumps(sorted(curr)))
    return curr
