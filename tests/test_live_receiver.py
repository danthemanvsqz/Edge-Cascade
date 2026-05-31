"""Unit tests for cascade.live_receiver's pure projection (nodes_for / node_delta).

These decide which node the dashboard lights and which transitions it pushes, so
a wrong mapping or a dropped/duplicated transition is the kind of silent
diagnostic error the 100% gate guards. The live app.events.Receiver loop + Redis
publish are live substrate (omitted), not exercised here.
"""
from __future__ import annotations

import json

from cascade.live_receiver import hold_remaining, node_delta, nodes_for, publish_state


def test_nodes_for_maps_known_skips_unknown():
    names = ["mesh.budget._gpu_solve", "mesh.budget._route", "flower.x", "celery.chord"]
    assert nodes_for(names) == {"gpu_solve", "route"}


def test_nodes_for_empty():
    assert nodes_for([]) == set()


def test_node_delta_active_and_idle():
    assert node_delta({"route"}, {"gpu_solve"}) == [("gpu_solve", "active"), ("route", "idle")]


def test_node_delta_no_change():
    assert node_delta({"draft"}, {"draft"}) == []


def test_node_delta_sorted_by_node():
    assert node_delta(set(), {"route", "draft", "gpu_solve"}) == [
        ("draft", "active"),
        ("gpu_solve", "active"),
        ("route", "active"),
    ]


class _FakePub:
    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.sets: list[tuple[str, str]] = []

    def publish(self, channel, msg):
        self.published.append((channel, msg))

    def set(self, key, val):
        self.sets.append((key, val))


def test_hold_remaining():
    assert hold_remaining(100.0, 100.0, 0.6) == 0.6  # just activated -> full hold
    assert round(hold_remaining(100.0, 100.5, 0.6), 6) == 0.1  # 0.5s elapsed
    assert hold_remaining(100.0, 200.0, 0.6) == 0.0  # long active -> no hold


def test_publish_state_publishes_deltas_and_sets_seed():
    pub = _FakePub()
    slept: list[float] = []
    result = publish_state(
        pub, "chan", "key", {"route"}, {"gpu_solve"},
        active_since={"route": 100.0}, now=100.0, min_lit=0.6, sleep=slept.append,
    )
    assert pub.published == [
        ("chan", json.dumps({"node": "gpu_solve", "state": "active"})),
        ("chan", json.dumps({"node": "route", "state": "idle"})),
    ]
    assert pub.sets == [("key", json.dumps(["gpu_solve"]))]
    assert result == {"gpu_solve"}
    # route went active at 100.0, idle at now=100.0 -> 0 elapsed -> held min_lit
    assert slept == [0.6]


def test_publish_state_records_activation_and_skips_hold_for_slow_nodes():
    pub = _FakePub()
    slept: list[float] = []
    since: dict[str, float] = {"gpu_solve": 100.0}
    # gpu_solve went idle after 100s active (>> min_lit) -> no hold; a new active
    # node records its activation time.
    publish_state(
        pub, "chan", "key", {"gpu_solve"}, {"draft"},
        active_since=since, now=200.0, min_lit=0.6, sleep=slept.append,
    )
    assert slept == []  # slow node not held
    assert since["draft"] == 200.0  # activation recorded
