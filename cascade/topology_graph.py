"""Canonical topology graph definitions for each Canvas topology.

Each topology defined in cascade.topologies_canvas has a corresponding
TopologyGraph here — the directed graph the dashboard renders. This is the
single source of truth for the dashboard layout; the Beat task in celery_app
publishes whichever is ACTIVE_GRAPH.

Adding a new experiment topology:
  1. Define its graph here (nodes + edges).
  2. Set ACTIVE_GRAPH = my_experiment_graph.
  3. Restart the worker — Beat pushes it on startup + every 30 s.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EdgeKind = Literal["flow", "alt", "repair", "cap", "parallel"]


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    tier: str   # "npu" | "verify" | "gpu" | "cloud" | "tier3"
    queue: str
    task: str | None  # Celery task name; None for sub-ops / synthetic nodes


@dataclass(frozen=True)
class GraphEdge:
    from_id: str
    to_id: str
    kind: EdgeKind


@dataclass(frozen=True)
class TopologyGraph:
    name: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "nodes": [
                {"id": n.id, "label": n.label, "tier": n.tier,
                 "queue": n.queue, "task": n.task}
                for n in self.nodes
            ],
            "edges": [
                {"from": e.from_id, "to": e.to_id, "kind": e.kind}
                for e in self.edges
            ],
        }


# ── budget ───────────────────────────────────────────────────────────────────
# Sequential cost-ordered cascade.
#
# Forward (no DSL): route → draft → verify_syntax → resolve_npu → gpu_solve
# Forward (DSL):    route → draft → verify_functional → resolve_npu → gpu_solve
# GPU repair loop:  gpu_solve → repair_prompt → verify_syntax (retry)
# Cap path:         gpu_solve → tier3 → cloud

_B = "mesh.budget."
BUDGET_GRAPH = TopologyGraph(
    name="budget",
    nodes=(
        GraphNode("route",             "route",         "npu",    "npu",    _B + "_route"),
        GraphNode("draft",             "draft",         "npu",    "npu",    _B + "_draft"),
        GraphNode("verify_syntax",     "verify_syntax", "verify", "verify", _B + "_verify"),
        GraphNode("verify_functional", "verify_func",   "verify", "verify", None),
        GraphNode("resolve_npu",       "resolve_npu",   "verify", "verify", _B + "_resolve_npu"),
        GraphNode("gpu_solve",         "gpu_solve",     "gpu",    "gpu",    _B + "_gpu_solve"),
        GraphNode("repair_prompt",     "repair_prompt", "verify", "verify", None),
        # _budget_done always runs last — logs WIN or LOSE regardless of path.
        # The agent (Tier 3) takes over when done logs capped->tier3.
        GraphNode("done",              "_done",         "verify", "verify", _B + "_done"),
        GraphNode("tier3",             "Tier 3 · CLI",  "tier3",  "—",  None),
        GraphNode("cloud",             "cloud",         "cloud",  "cloud",  None),
    ),
    edges=(
        GraphEdge("route",             "draft",             "flow"),
        GraphEdge("draft",             "verify_syntax",     "flow"),   # no-DSL path
        GraphEdge("draft",             "verify_functional", "alt"),    # DSL path
        GraphEdge("verify_syntax",     "resolve_npu",       "flow"),
        GraphEdge("verify_functional", "resolve_npu",       "alt"),
        GraphEdge("resolve_npu",       "gpu_solve",         "flow"),
        # GPU repair loop: gpu_solve calls verify as its internal gate check,
        # then on FAIL builds repair_prompt and retries gpu_solve.
        GraphEdge("gpu_solve",         "verify_syntax",     "repair"),
        GraphEdge("gpu_solve",         "verify_functional", "repair"),
        GraphEdge("verify_syntax",     "repair_prompt",     "repair"),
        GraphEdge("repair_prompt",     "gpu_solve",         "repair"),
        # Win/lose logger always runs last (after _budget_cloud no-op).
        GraphEdge("gpu_solve",         "done",              "flow"),
        # Cap: agent takes over when done logs capped->tier3.
        GraphEdge("done",              "tier3",             "cap"),
        GraphEdge("tier3",             "cloud",             "cap"),
    ),
)

# ── low_latency ───────────────────────────────────────────────────────────────
# NPU draft races GPU generate concurrently (a Celery chord).
# No repair loop — trades GPU cost for wall-time. Double-miss → tier3 directly.

LOW_LATENCY_GRAPH = TopologyGraph(
    name="low_latency",
    nodes=(
        GraphNode("npu_draft",  "npu_draft",     "npu",    "npu",    "mesh.budget._draft"),
        GraphNode("gpu_swap",   "gpu_swap",      "gpu",    "gpu",    "model.swap_task"),
        GraphNode("gpu_gen",    "gpu_generate",  "gpu",    "gpu",    "mesh.generate_qwen14b"),
        GraphNode("pick",       "_pick_first",   "verify", "verify", "mesh.low_latency._pick"),
        GraphNode("tier3",      "Tier 3 · CLI",  "tier3",  "—",      None),
        GraphNode("cloud",      "cloud",         "cloud",  "cloud",  None),
    ),
    edges=(
        GraphEdge("npu_draft", "pick",    "parallel"),  # racing arm 1
        GraphEdge("gpu_swap",  "gpu_gen", "parallel"),  # racing arm 2a
        GraphEdge("gpu_gen",   "pick",    "parallel"),  # racing arm 2b
        GraphEdge("pick",      "tier3",   "cap"),
        GraphEdge("tier3",     "cloud",   "cap"),
    ),
)

# ── model selection experiment topologies (git + cli) ────────────────────────
# Both share the same 4-GPU-model fan-out shape; differ only by name so the
# dashboard and TOPOLOGY_GRAPHS registry can distinguish which bench is live.

GIT_MODEL_SELECTION_GRAPH = TopologyGraph(
    name="git_model_selection",
    nodes=(
        GraphNode("route",      "task router",          "verify", "verify", None),
        GraphNode("qwen_14b",   "qwen2.5-coder:14b",   "gpu",    "gpu",    None),
        GraphNode("r1_14b",     "deepseek-r1:14b",      "gpu",    "gpu",    None),
        GraphNode("coder_6b",   "deepseek-coder:6.7b",  "gpu",    "gpu",    None),
        GraphNode("qwen_7b",    "qwen2.5-coder:7b",     "gpu",    "gpu",    None),
        GraphNode("gate",       "struct-gate",           "verify", "verify", None),
        GraphNode("done",       "_done",                 "verify", "verify", None),
    ),
    edges=(
        GraphEdge("route",    "qwen_14b", "flow"),
        GraphEdge("route",    "r1_14b",   "alt"),
        GraphEdge("route",    "coder_6b", "alt"),
        GraphEdge("route",    "qwen_7b",  "alt"),
        GraphEdge("qwen_14b", "gate",     "parallel"),
        GraphEdge("r1_14b",   "gate",     "parallel"),
        GraphEdge("coder_6b", "gate",     "parallel"),
        GraphEdge("qwen_7b",  "gate",     "parallel"),
        GraphEdge("gate",     "done",     "flow"),
    ),
)

CLI_MODEL_SELECTION_GRAPH = TopologyGraph(
    name="cli_model_selection",
    nodes=(
        GraphNode("route",      "task router",          "verify", "verify", None),
        GraphNode("qwen_14b",   "qwen2.5-coder:14b",   "gpu",    "gpu",    None),
        GraphNode("r1_14b",     "deepseek-r1:14b",      "gpu",    "gpu",    None),
        GraphNode("coder_6b",   "deepseek-coder:6.7b",  "gpu",    "gpu",    None),
        GraphNode("qwen_7b",    "qwen2.5-coder:7b",     "gpu",    "gpu",    None),
        GraphNode("gate",       "struct-gate",           "verify", "verify", None),
        GraphNode("done",       "_done",                 "verify", "verify", None),
    ),
    edges=(
        GraphEdge("route",    "qwen_14b", "flow"),
        GraphEdge("route",    "r1_14b",   "alt"),
        GraphEdge("route",    "coder_6b", "alt"),
        GraphEdge("route",    "qwen_7b",  "alt"),
        GraphEdge("qwen_14b", "gate",     "parallel"),
        GraphEdge("r1_14b",   "gate",     "parallel"),
        GraphEdge("coder_6b", "gate",     "parallel"),
        GraphEdge("qwen_7b",  "gate",     "parallel"),
        GraphEdge("gate",     "done",     "flow"),
    ),
)

# ── budget_fanout ─────────────────────────────────────────────────────────────
# Conceptual topology for agent-driven decompose + parallel budget cascade.
# Tier 3 (agent) decomposes the prompt, dispatches N budget cascade instances
# in parallel, then integrates the sub-results. This graph shows the shape on
# the dashboard; the actual parallel dispatch is `solve_budget_fanout` in
# canvas_client.py (not a single Celery signature — N independent chains).

BUDGET_FANOUT_GRAPH = TopologyGraph(
    name="budget_fanout",
    nodes=(
        GraphNode("decompose", "decompose",  "tier3",  "—",      None),
        GraphNode("budget_0",  "budget[0]",  "gpu",    "gpu",    None),
        GraphNode("budget_1",  "budget[1]",  "gpu",    "gpu",    None),
        GraphNode("budget_n",  "budget[N]",  "gpu",    "gpu",    None),
        GraphNode("merge",     "merge",      "tier3",  "—",      None),
    ),
    edges=(
        GraphEdge("decompose", "budget_0", "parallel"),
        GraphEdge("decompose", "budget_1", "parallel"),
        GraphEdge("decompose", "budget_n", "parallel"),
        GraphEdge("budget_0",  "merge",    "parallel"),
        GraphEdge("budget_1",  "merge",    "parallel"),
        GraphEdge("budget_n",  "merge",    "parallel"),
    ),
)

# Active graph — what the Beat task publishes. Swap for experiment topologies.
ACTIVE_GRAPH: TopologyGraph = BUDGET_GRAPH

# Registry for lookup by name (used by the Beat task to select by topology name)
TOPOLOGY_GRAPHS: dict[str, TopologyGraph] = {
    "budget":              BUDGET_GRAPH,
    "budget_fanout":       BUDGET_FANOUT_GRAPH,
    "low_latency":         LOW_LATENCY_GRAPH,
    "git_model_selection": GIT_MODEL_SELECTION_GRAPH,
    "cli_model_selection": CLI_MODEL_SELECTION_GRAPH,
}
