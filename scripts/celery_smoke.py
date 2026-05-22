"""Proof-of-life for the Celery substrate (C1 Phase-0).

Dispatches `verify_functional` over Redis on a CORRECT and a BUGGY dijkstra and
prints the verdicts. Proves a tier op runs as a Celery task end-to-end and
writes runs/edge-verify.rec -- without any GPU/NPU (the gate is pure). The
`generate` tier needs Ollama; this smoke deliberately exercises only the gate.

Setup:
    docker compose up -d redis
    uv sync --extra celery
    uv run celery -A cascade.celery_app worker -Q verify -l info   # in one shell
    uv run python scripts/celery_smoke.py                          # in another
"""
from __future__ import annotations

from cascade.tasks import verify_functional_task

GOOD = """```python
import heapq
def dijkstra(graph, start):
    nodes = set(graph) | {n for nb in graph.values() for n in nb}
    dist = {n: float('inf') for n in nodes}
    dist[start] = 0
    pq = [(0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        for v, w in graph.get(u, {}).items():
            if d + w < dist[v]:
                dist[v] = d + w
                heapq.heappush(pq, (d + w, v))
    return {n: d for n, d in dist.items() if d != float('inf')}
```"""

BAD = """```python
def dijkstra(graph, start):
    dist = {n: float('inf') for n in graph}   # BUG: sink node 'E' never initialised
    dist[start] = 0
    for u in graph:
        for v, w in graph[u].items():
            if dist[u] + w < dist[v]:          # KeyError on 'E'
                dist[v] = dist[u] + w
    return dist
```"""


def main() -> None:
    for label, code in (("good", GOOD), ("bad", BAD)):
        res = verify_functional_task.delay(code).get(timeout=60)
        obs = [f.get("observed") for f in res.get("failures", [])]
        print(f"{label:>5}: passed={res.get('passed')} checked={res.get('checked')} "
              f"failures={obs}")
    print("\nDispatched over Redis; runs/edge-verify.rec should have grown "
          "(same grammar as the pipe path).")


if __name__ == "__main__":
    main()
