"""Tests for cascade/health.py -- EDGE-1a launch-time dependency probes.

Covers: each probe's UP/DOWN branches via injected fakes, the never-raise
contract of @_probe, the real default I/O callables (patched at the stdlib /
library seam), plan_repairs' reprobe-first rule, exit_code, and rendering.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from cascade import health
from cascade.health import (
    Status,
    exit_code,
    format_table,
    plan_repairs,
    probe_all,
    probe_dashboard,
    probe_docker,
    probe_ollama,
    probe_redis,
    probe_worker,
    render,
)

ORDER = ["docker", "redis", "ollama", "worker", "dashboard"]


def _proc(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=out, stderr=err)


def _statuses(**down: bool) -> list[Status]:
    """All five deps UP, except the names passed as `name=True` (DOWN)."""
    critical = {"docker", "redis", "worker"}
    return [Status(n, not down.get(n, False), n in critical, "d") for n in ORDER]


# --- probes (injected fakes) -------------------------------------------------

def test_docker_up():
    s = probe_docker(run=lambda argv: _proc(0, "28.3.2\n"))
    assert s == Status("docker", True, True, "engine 28.3.2")


def test_docker_down_reports_last_stderr_line():
    s = probe_docker(run=lambda argv: _proc(1, err="warn\nCannot connect to the Docker daemon\n"))
    assert (s.up, s.detail) == (False, "Cannot connect to the Docker daemon")


def test_docker_down_without_stderr_reports_exit_code():
    assert probe_docker(run=lambda argv: _proc(3)).detail == "docker info exited 3"


def test_docker_missing_binary_is_down_not_raised():
    def run(argv):
        raise FileNotFoundError("docker")
    s = probe_docker(run=run)
    assert (s.up, s.detail) == (False, "FileNotFoundError: docker")


def test_redis_up_and_falsy_ping():
    assert probe_redis(ping=lambda: True) == Status("redis", True, True, "PONG")
    assert probe_redis(ping=lambda: False).up is False


def test_redis_connection_error_is_down():
    def ping():
        raise ConnectionError("refused")
    assert probe_redis(ping=ping).detail == "ConnectionError: refused"


def test_ollama_up_is_not_critical():
    s = probe_ollama(get=lambda url: '{"version":"0.34.2"}')
    assert s == Status("ollama", True, False, "v0.34.2")


def test_ollama_hits_configured_base_url():
    seen = []
    probe_ollama(get=lambda url: seen.append(url) or '{"version":"1"}')
    assert seen == [f"{health.CONFIG.ollama_base_url}/api/version"]


def test_ollama_down():
    def get(url):
        raise OSError("refused")
    assert probe_ollama(get=get).up is False


@pytest.mark.parametrize("reply", [None, {}])
def test_worker_no_nodes_is_down(reply):
    s = probe_worker(ping=lambda: reply)
    assert (s.up, s.detail) == (False, "no Celery node answered inspect ping")


def test_worker_one_node():
    s = probe_worker(ping=lambda: {"edge-local@box": {"ok": "pong"}})
    assert (s.up, s.detail) == (True, "edge-local@box")


def test_worker_duplicates_are_flagged():
    s = probe_worker(ping=lambda: {"b@x": {}, "a@x": {}})
    assert s.up and s.detail == "a@x, b@x -- 2 nodes, duplicate workers?"


def test_dashboard_open_and_closed():
    assert probe_dashboard(is_open=lambda port: True) == Status(
        "dashboard", True, False, "http://localhost:8789")
    assert probe_dashboard(is_open=lambda port: False).up is False


# --- default I/O callables (patched at the library seam) ----------------------

def test_default_run_calls_subprocess(mocker):
    run = mocker.patch("cascade.health.subprocess.run", return_value=_proc(0, "1"))
    assert probe_docker().up
    run.assert_called_once_with(["docker", "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True, timeout=15)


def test_default_redis_ping(mocker):
    from_url = mocker.patch("redis.Redis.from_url")
    from_url.return_value.ping.return_value = True
    mocker.patch("cascade.celery_app.REDIS_URL", "redis://h:1/0")
    assert probe_redis().up
    from_url.assert_called_once_with("redis://h:1/0", socket_connect_timeout=2, socket_timeout=2)


def test_default_worker_ping(mocker):
    app = mocker.patch("cascade.celery_app.app")
    app.control.inspect.return_value.ping.return_value = {"n@x": {}}
    assert probe_worker().detail == "n@x"
    app.control.inspect.assert_called_once_with(timeout=2)


def test_default_http_get(mocker):
    resp = mocker.MagicMock()
    resp.__enter__.return_value.read.return_value = b'{"version":"9"}'
    urlopen = mocker.patch("cascade.health.urllib.request.urlopen", return_value=resp)
    assert probe_ollama().detail == "v9"
    assert urlopen.call_args.kwargs == {"timeout": 3}


def test_default_tcp_open(mocker):
    conn = mocker.patch("cascade.health.socket.create_connection")
    assert probe_dashboard().up
    conn.assert_called_once_with(("127.0.0.1", 8789), timeout=0.5)


def _fake_probes(calls: list[str], **down: bool):
    """Probes shaped like the @_probe ones, recording which were actually called."""
    def make(name: str, critical: bool):
        def probe() -> Status:
            calls.append(name)
            return Status(name, not down.get(name, False), critical, "d")
        probe.dep_name, probe.critical = name, critical
        return probe
    return tuple(make(n, n in {"docker", "redis", "worker"}) for n in ORDER)


def test_probe_all_order(mocker):
    calls: list[str] = []
    mocker.patch.object(health, "PROBES", _fake_probes(calls))
    assert [s.name for s in probe_all()] == ORDER
    assert calls == ORDER


def test_probe_all_skips_dependant_of_a_down_prereq():
    calls: list[str] = []
    statuses = probe_all(probes=_fake_probes(calls, redis=True))
    assert "worker" not in calls  # no ~8s ping against a dead broker
    worker = statuses[ORDER.index("worker")]
    assert worker == Status("worker", False, True, "not probed: redis is down")


def test_probe_all_skip_cascades_down_the_chain():
    calls: list[str] = []
    statuses = probe_all(probes=_fake_probes(calls, docker=True))
    assert calls == ["docker", "ollama", "dashboard"]
    assert plan_repairs(statuses) == [("docker", False), ("redis", True), ("worker", True)]


def test_probe_all_only_probes_the_named_deps():
    calls: list[str] = []
    statuses = probe_all(only={"worker", "ollama"}, probes=_fake_probes(calls))
    assert [s.name for s in statuses] == calls == ["ollama", "worker"]


def test_probe_all_only_does_not_skip_when_prereq_unprobed():
    # --only worker is edge-cli's wait loop *after* redis is back: it must ping.
    calls: list[str] = []
    probe_all(only={"worker"}, probes=_fake_probes(calls, redis=True))
    assert calls == ["worker"]


def test_default_probes_are_in_dependency_order():
    assert [p.__name__ for p in health.PROBES] == [f"probe_{n}" for n in ORDER]
    assert [p.dep_name for p in health.PROBES] == ORDER
    assert {p.dep_name for p in health.PROBES if p.critical} == {"docker", "redis", "worker"}


# --- plan_repairs / exit_code / render ----------------------------------------

def test_plan_all_up_is_empty():
    assert plan_repairs(_statuses()) == []


def test_plan_reprobes_dependants_of_a_down_prereq():
    plan = plan_repairs(_statuses(docker=True, redis=True, worker=True))
    assert plan == [("docker", False), ("redis", True), ("worker", True)]


def test_plan_starts_dependant_directly_when_prereq_is_up():
    plan = plan_repairs(_statuses(ollama=True, worker=True, dashboard=True))
    assert plan == [("ollama", False), ("worker", False), ("dashboard", False)]


@pytest.mark.parametrize("down,code", [
    ({}, 0),
    ({"ollama": True, "dashboard": True}, 0),
    ({"worker": True}, 1),
    ({"docker": True}, 1),
])
def test_exit_code_only_critical_fails(down, code):
    assert exit_code(_statuses(**down)) == code


def test_format_table():
    table = format_table([Status("redis", False, True, "refused"),
                          Status("ollama", True, False, "v1")])
    assert table.splitlines() == [
        "redis      DOWN  (critical)  refused",
        "ollama     UP     v1",
    ]


def test_render_json_carries_statuses_and_plan():
    doc = json.loads(render(_statuses(redis=True), as_json=True))
    assert doc["repairs"] == [["redis", False]]
    assert doc["statuses"][1] == {"name": "redis", "up": False, "critical": True, "detail": "d"}
    assert doc["prereqs"] == {"redis": "docker", "worker": "redis"}


def test_render_text_is_the_table():
    statuses = _statuses()
    assert render(statuses, as_json=False) == format_table(statuses)
