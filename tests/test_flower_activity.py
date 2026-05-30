"""Unit tests for cascade.flower_activity -- the pure live-activity projection.

The parse/map logic decides *which chain node the dashboard shows as spinning*,
so a wrong filter or mapping is exactly the "diagnostic looks fine, real signal
lost" failure the 100% gate exists to catch. The httpx boundary in snapshot()
is mocked (monkeypatch) -- the parse logic under it is real, so this is a real
test of the never-raise contract, not mock-theater.
"""
from __future__ import annotations

import httpx

from cascade import flower_activity as fa
from cascade.flower_activity import ActiveTask, active_nodes, parse_active, snapshot


def test_parse_active_keeps_started_maps_node():
    tasks = {
        "u1": {
            "name": "mesh.balanced._gpu_solve",
            "state": "STARTED",
            "started": 100.0,
            "uuid": "u1",
            "worker": "w1",
        }
    }
    (t,) = parse_active(tasks, now=160.0)
    assert (t.node, t.tier, t.task_id, t.worker) == ("gpu_solve", "gpu", "u1", "w1")
    assert t.runtime_s == 60.0


def test_parse_active_skips_non_started():
    tasks = {"u1": {"name": "mesh.balanced._gpu_solve", "state": "SUCCESS", "started": 100.0}}
    assert parse_active(tasks, now=160.0) == []


def test_parse_active_skips_unknown_task():
    tasks = {"u1": {"name": "flower.unrelated", "state": "STARTED", "started": 100.0}}
    assert parse_active(tasks, now=160.0) == []


def test_parse_active_falsy_started_zero_runtime():
    tasks = {"u1": {"name": "mesh.balanced._route", "state": "STARTED", "started": 0}}
    (t,) = parse_active(tasks, now=160.0)
    assert t.runtime_s == 0.0  # not `now`


def test_parse_active_missing_worker_uuid_defaults():
    tasks = {"k9": {"name": "mesh.balanced._draft", "state": "STARTED", "started": 100.0}}
    (t,) = parse_active(tasks, now=160.0)
    assert t.worker == ""
    assert t.task_id == "k9"  # falls back to the dict key when uuid absent


def test_active_nodes_returns_node_set():
    snap = [
        ActiveTask("mesh.balanced._route", "route", "npu", "a", "w", 1.0),
        ActiveTask("mesh.balanced._gpu_solve", "gpu_solve", "gpu", "b", "w", 2.0),
    ]
    assert active_nodes(snap) == {"route", "gpu_solve"}


class _FakeResp:
    def __init__(self, status_code, payload=None, raise_on_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("bad json")
        return self._payload


def test_snapshot_success(monkeypatch):
    payload = {"u1": {"name": "mesh.balanced._gpu_solve", "state": "STARTED", "started": 0, "uuid": "u1"}}
    monkeypatch.setattr(fa.httpx, "get", lambda url, timeout: _FakeResp(200, payload))
    (t,) = snapshot()
    assert t.node == "gpu_solve"


def test_snapshot_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(fa.httpx, "get", lambda url, timeout: _FakeResp(503))
    assert snapshot() == []


def test_snapshot_httpx_error_returns_empty(monkeypatch):
    def boom(url, timeout):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(fa.httpx, "get", boom)
    assert snapshot() == []


def test_snapshot_bad_json_returns_empty(monkeypatch):
    monkeypatch.setattr(fa.httpx, "get", lambda url, timeout: _FakeResp(200, raise_on_json=True))
    assert snapshot() == []


def test_snapshot_non_dict_json_returns_empty(monkeypatch):
    # 200 + valid JSON that isn't an object: must not AttributeError out of the loop.
    monkeypatch.setattr(fa.httpx, "get", lambda url, timeout: _FakeResp(200, payload=[]))
    assert snapshot() == []


def test_sample_occupancy_counts_one_interval(monkeypatch):
    # monotonic: deadline calc (0.0 -> deadline 1.0), enter loop (0.0 < 1.0),
    # exit loop (99.0 >= 1.0) -> exactly one sampled interval.
    clock = iter([0.0, 0.0, 99.0])
    monkeypatch.setattr(fa.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(fa.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        fa,
        "snapshot",
        lambda base_url: [ActiveTask("mesh.balanced._gpu_solve", "gpu_solve", "gpu", "u1", "w", 5.0)],
    )
    assert fa.sample_occupancy(duration_s=1.0, hz=1.0) == {"gpu_solve": 1.0}
