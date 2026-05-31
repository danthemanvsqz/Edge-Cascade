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


def test_publish_state_fast_idle_deferred_not_published():
    """A fast node's idle is deferred into pending_idles, not published immediately."""
    pub = _FakePub()
    pending: dict[str, float] = {}
    result = publish_state(
        pub, "chan", "key", {"route"}, {"gpu_solve"},
        active_since={"route": 100.0}, now=100.0, pending_idles=pending, min_lit=0.6,
    )
    # gpu_solve goes active (published), route idle deferred (not published yet)
    assert pub.published == [("chan", json.dumps({"node": "gpu_solve", "state": "active"}))]
    assert result == {"gpu_solve"}
    assert pending == {"route": 100.6}
    # seed includes both curr and the still-lit deferred node
    assert pub.sets == [("key", json.dumps(["gpu_solve", "route"]))]


def test_publish_state_pending_idle_flushed_on_next_call():
    """Deferred idle is published when now >= not_before on a subsequent call."""
    pub = _FakePub()
    pending: dict[str, float] = {"route": 100.6}  # seed from previous call
    publish_state(
        pub, "chan", "key", {"gpu_solve"}, {"gpu_solve"},
        active_since={}, now=101.0, pending_idles=pending, min_lit=0.6,
    )
    # Route idle flushed (101.0 >= 100.6); no new transitions
    assert ("chan", json.dumps({"node": "route", "state": "idle"})) in pub.published
    assert pending == {}


def test_publish_state_active_cancels_pending_idle():
    """A node that goes active again while its idle is pending gets the idle cancelled."""
    pub = _FakePub()
    pending: dict[str, float] = {"route": 100.6}
    publish_state(
        pub, "chan", "key", set(), {"route"},
        active_since={}, now=100.1, pending_idles=pending, min_lit=0.6,
    )
    # route went active at now=100.1 (before not_before=100.6) -> idle cancelled
    assert pending == {}
    assert ("chan", json.dumps({"node": "route", "state": "active"})) in pub.published
    # no idle published
    assert not any("idle" in msg for _, msg in pub.published)


def test_publish_state_slow_idle_published_immediately():
    """A slow node (lit > min_lit) gets its idle published immediately, not deferred."""
    pub = _FakePub()
    pending: dict[str, float] = {}
    since: dict[str, float] = {"gpu_solve": 100.0}
    publish_state(
        pub, "chan", "key", {"gpu_solve"}, {"draft"},
        active_since=since, now=200.0, pending_idles=pending, min_lit=0.6,
    )
    assert pending == {}  # no deferral for slow node
    assert since["draft"] == 200.0  # activation recorded
    assert ("chan", json.dumps({"node": "gpu_solve", "state": "idle"})) in pub.published
    assert ("chan", json.dumps({"node": "draft", "state": "active"})) in pub.published
