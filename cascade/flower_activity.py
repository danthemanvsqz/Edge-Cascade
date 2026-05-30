"""Read-only Flower activity probe -- the decoupled live-cascade-activity source.

One source of truth for "which chain node is spinning right now", read from
Flower's REST API (`/api/tasks`) rather than written into `runs/cascade.rec`.
The `.rec` stream only records *completed* outcomes, so it cannot light up a
node while `gpu_solve` actually grinds (~60s); Flower captures STARTED tasks
live via worker events (the Canvas worker must run with `-E`).

This module is pure parse + mapping: no network, no broker writes, no Celery
import. The HTTP fetch lives in the thin consumers (debug CLI, experiments,
the dashboard `/active` endpoint) so this core stays trivially testable and
safe to import from anywhere (OBS-1, Flower-backed variant).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

FLOWER_URL = "http://127.0.0.1:5555"

# Celery task name -> (chain-node id, tier). The chain tasks are all named
# `mesh.<topology>._<node>` in cascade/topologies_canvas.py; this is the only
# coupling to the chain's shape and the one place to extend for new nodes.
NODE_BY_TASK: dict[str, tuple[str, str]] = {
    "mesh.balanced._route": ("route", "npu"),
    "mesh.balanced._draft": ("draft", "npu"),
    "mesh.balanced._draft_gate": ("draft_gate", "verify"),
    "mesh.balanced._gpu_solve": ("gpu_solve", "gpu"),
    "mesh.balanced._merge_gpu": ("merge_gpu", "gpu"),
    "mesh.balanced._done": ("done", "verify"),
    "mesh.low_latency._pick": ("pick", "verify"),
}


@dataclass(frozen=True)
class ActiveTask:
    task_name: str
    node: str
    tier: str
    task_id: str
    worker: str
    runtime_s: float


def parse_active(tasks: dict[str, dict], now: float) -> list[ActiveTask]:
    """Project a Flower `/api/tasks` response onto the cascade's active nodes.

    `tasks` is keyed by task uuid; each value carries `name`, `state`,
    `started`, `worker`, `uuid`. Keep only STARTED tasks whose name maps to a
    known chain node (drops Flower's own / unrelated tasks). `runtime_s` is
    elapsed-since-started, or 0.0 when Flower has no start stamp yet.
    """
    out: list[ActiveTask] = []
    for key, value in tasks.items():
        if value.get("state") != "STARTED":
            continue
        mapped = NODE_BY_TASK.get(value.get("name", ""))
        if mapped is None:
            continue
        node, tier = mapped
        started = value.get("started")
        runtime_s = now - started if started else 0.0
        out.append(
            ActiveTask(
                task_name=value["name"],
                node=node,
                tier=tier,
                task_id=value.get("uuid", key),
                worker=value.get("worker", ""),
                runtime_s=runtime_s,
            )
        )
    return out


def active_nodes(snap: list[ActiveTask]) -> set[str]:
    """The set of chain-node ids currently executing."""
    return {task.node for task in snap}


def snapshot(base_url: str = FLOWER_URL, timeout: float = 2.0) -> list[ActiveTask]:
    """Fetch Flower's live task table and project it onto active chain nodes.

    The one shared fetch for every consumer (debug CLI, experiments, the
    dashboard `/active` endpoint). Read-only and **never raises**: a down
    Flower, a timeout, or a malformed body all yield an empty list, so a 2 Hz
    poll loop can't be taken down by the probe.
    """
    try:
        resp = httpx.get(f"{base_url}/api/tasks", timeout=timeout)
        if resp.status_code != 200:
            return []
        return parse_active(resp.json(), now=time.time())
    except (httpx.HTTPError, ValueError):
        return []
