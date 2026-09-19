"""Launch-time health probes for the Canvas pipeline's dependencies (EDGE-1a).

`edge` (scripts/edge-cli.ps1) must guarantee the pipeline is up before it
launches a session. This module is the decision half of that supervisor: pure,
covered probes that report each dependency's state, plus `plan_repairs()` which
says what to (re)start and in what order. Process spawning stays in the
PowerShell glue (EDGE-1b) -- nothing here starts anything.

Dependencies, in DEPENDENCY order (a later one may need an earlier one):

    docker     Docker engine              critical  (hosts redis)
    redis      Celery broker + backend    critical  (PING on cascade.celery_app URL)
    ollama     Ollama API :11434          optional  (experiment / fallback backend)
    worker     Celery npu,gpu,verify node critical  (inspect ping answers >= 1 node)
    dashboard  SD-3 dashboard :8789       optional

Every probe takes its I/O as an injectable callable (default = the real call)
and is wrapped by `@_probe`, which turns ANY exception into a DOWN status with
the error as the detail -- a probe never raises, so one broken dependency can't
hide the others' state.

    uv run python -m cascade.health          # table; exit 1 iff a critical dep is down
    uv run python -m cascade.health --json   # machine-readable, for the PS glue
    uv run python -m cascade.health --json --only worker   # one dep (wait loops)
"""
from __future__ import annotations

import argparse
import functools
import json
import socket
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass

from cascade.config import CONFIG

DASHBOARD_PORT = 8789

# dependency -> the dependency it needs. When both are down, the dependant is
# re-probed after its prerequisite comes back instead of started blindly: the
# compose redis has `restart: unless-stopped` (comes back with the engine) and a
# live Celery worker reconnects to a restarted broker on its own -- starting a
# second one would be the duplicate-worker bug EDGE-1 exists to fix.
PREREQS: dict[str, str] = {"redis": "docker", "worker": "redis"}


@dataclass(frozen=True)
class Status:
    name: str
    up: bool
    critical: bool
    detail: str


def _probe(name: str, critical: bool):
    """Decorator: a `() -> (up, detail)` check becomes a never-raising `-> Status`."""
    def deco(check: Callable[..., tuple[bool, str]]) -> Callable[..., Status]:
        @functools.wraps(check)
        def wrapper(*args, **kwargs) -> Status:
            try:
                up, detail = check(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- any failure means DOWN
                up, detail = False, f"{type(exc).__name__}: {exc}"
            return Status(name, up, critical, detail)
        wrapper.dep_name, wrapper.critical = name, critical
        return wrapper
    return deco


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def _redis_ping() -> bool:
    import redis  # lazy: the `celery` extra

    from cascade.celery_app import REDIS_URL
    return bool(redis.Redis.from_url(REDIS_URL, socket_connect_timeout=2,
                                     socket_timeout=2).ping())


def _worker_ping() -> dict | None:
    from cascade.celery_app import app  # lazy: the `celery` extra
    return app.control.inspect(timeout=2).ping()


def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=3) as resp:
        return resp.read().decode()


def _tcp_open(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
        return True


@_probe("docker", critical=True)
def probe_docker(run: Callable[[list[str]], subprocess.CompletedProcess] = _run):
    proc = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if proc.returncode == 0:
        return True, f"engine {proc.stdout.strip()}"
    lines = (proc.stderr or "").strip().splitlines()
    return False, lines[-1] if lines else f"docker info exited {proc.returncode}"


@_probe("redis", critical=True)
def probe_redis(ping: Callable[[], bool] = _redis_ping):
    return (True, "PONG") if ping() else (False, "PING returned falsy")


@_probe("ollama", critical=False)
def probe_ollama(get: Callable[[str], str] = _http_get):
    version = json.loads(get(f"{CONFIG.ollama_base_url}/api/version"))["version"]
    return True, f"v{version}"


@_probe("worker", critical=True)
def probe_worker(ping: Callable[[], dict | None] = _worker_ping):
    nodes = sorted(ping() or {})
    if not nodes:
        return False, "no Celery node answered inspect ping"
    dup = f" -- {len(nodes)} nodes, duplicate workers?" if len(nodes) > 1 else ""
    return True, ", ".join(nodes) + dup


@_probe("dashboard", critical=False)
def probe_dashboard(is_open: Callable[[int], bool] = _tcp_open):
    return is_open(DASHBOARD_PORT), f"http://localhost:{DASHBOARD_PORT}"


PROBES: tuple[Callable[[], Status], ...] = (
    probe_docker, probe_redis, probe_ollama, probe_worker, probe_dashboard)


def probe_all(only: set[str] | None = None,
              probes: tuple[Callable[[], Status], ...] | None = None) -> list[Status]:
    """Each dependency's Status (all, or just the names in `only`), in dependency order.

    A dependency whose prerequisite is already DOWN is reported DOWN without
    being probed: a worker ping against a dead broker burns Celery's ~8s
    connection retry just to say what the redis line already says.
    """
    statuses: list[Status] = []
    down: set[str] = set()
    for probe in PROBES if probes is None else probes:
        if only is not None and probe.dep_name not in only:
            continue
        prereq = PREREQS.get(probe.dep_name)
        status = (Status(probe.dep_name, False, probe.critical, f"not probed: {prereq} is down")
                  if prereq in down else probe())
        if not status.up:
            down.add(status.name)
        statuses.append(status)
    return statuses


def plan_repairs(statuses: list[Status]) -> list[tuple[str, bool]]:
    """`(name, reprobe_first)` for each DOWN dependency, in input order.

    `reprobe_first` is True when the dependency's prerequisite is also down:
    re-probe it once the prerequisite is back, and start it only if still down.
    """
    down = {s.name for s in statuses if not s.up}
    return [(s.name, PREREQS.get(s.name) in down) for s in statuses if not s.up]


def exit_code(statuses: list[Status]) -> int:
    """0 iff every critical dependency is up -- non-critical ones only warn."""
    return int(any(s.critical and not s.up for s in statuses))


def format_table(statuses: list[Status]) -> str:
    return "\n".join(
        f"{s.name:<10} {'UP' if s.up else 'DOWN':<5}"
        f"{' (critical)' if s.critical and not s.up else ''}  {s.detail}"
        for s in statuses)


def render(statuses: list[Status], as_json: bool) -> str:
    if as_json:
        return json.dumps({"statuses": [asdict(s) for s in statuses],
                           "repairs": plan_repairs(statuses), "prereqs": PREREQS})
    return format_table(statuses)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover -- launcher glue
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit JSON for edge-cli")
    parser.add_argument("--only", action="append", choices=[p.dep_name for p in PROBES],
                        help="probe just this dependency (repeatable); edge-cli's wait loop")
    args = parser.parse_args(argv)  # before probing: --help must not pay probe cost
    statuses = probe_all(set(args.only) if args.only else None)
    print(render(statuses, args.json))
    return exit_code(statuses)


if __name__ == "__main__":  # pragma: no cover -- launcher glue
    sys.exit(main())
