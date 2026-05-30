"""Unit tests for cascade.live_receiver's pure projection (nodes_for / node_delta).

These decide which node the dashboard lights and which transitions it pushes, so
a wrong mapping or a dropped/duplicated transition is the kind of silent
diagnostic error the 100% gate guards. The live app.events.Receiver loop + Redis
publish are live substrate (omitted), not exercised here.
"""
from __future__ import annotations

import json

from cascade.live_receiver import node_delta, nodes_for, publish_state


def test_nodes_for_maps_known_skips_unknown():
    names = ["mesh.balanced._gpu_solve", "mesh.balanced._route", "flower.x", "celery.chord"]
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


def test_publish_state_publishes_deltas_and_sets_seed():
    pub = _FakePub()
    result = publish_state(pub, "chan", "key", {"route"}, {"gpu_solve"})
    assert pub.published == [
        ("chan", json.dumps({"node": "gpu_solve", "state": "active"})),
        ("chan", json.dumps({"node": "route", "state": "idle"})),
    ]
    assert pub.sets == [("key", json.dumps(["gpu_solve"]))]
    assert result == {"gpu_solve"}
