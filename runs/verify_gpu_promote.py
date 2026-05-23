"""Verification harness (transient): drive the REAL edge-gpu + edge-verify MCP
servers over stdio, exactly as the Tier-3 agent does, to prove that
CASCADE_GPU_MODEL selects the Tier-2 model and that the GPU tier produces code
that passes the deterministic functional gate.

Usage (run with the repo venv python):
    python runs/verify_gpu_promote.py ENV                 # use .env (promoted 7b)
    python runs/verify_gpu_promote.py qwen2.5-coder:14b   # control override
    python runs/verify_gpu_promote.py bogus-model:1b      # unavailable probe
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

TASK = (
    "Write a Python function def dijkstra(graph, start) that returns a dict of "
    "shortest-path costs from start for a directed weighted graph given as "
    "{node: {neighbor: weight}}."
)


def server(mod: str, model: str | None = None) -> StdioServerParameters:
    env = dict(os.environ)
    if model is not None:
        env["CASCADE_GPU_MODEL"] = model
    return StdioServerParameters(
        command=sys.executable, args=["-m", mod], cwd=str(ROOT), env=env)


def unwrap(res) -> dict:
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        return sc.get("result", sc)
    text = "".join(getattr(c, "text", "") for c in res.content)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {}


async def gpu_call(model_override: str | None) -> tuple[dict, dict]:
    async with stdio_client(server("mcp_servers.gpu", model_override)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            st = unwrap(await s.call_tool("status", {}))
            if not st.get("available"):
                return st, {}
            gen = unwrap(await s.call_tool("generate", {"prompt": TASK}))
            return st, gen


async def verify_text(text: str) -> tuple[dict, dict]:
    async with stdio_client(server("mcp_servers.verify")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            syn = unwrap(await s.call_tool("verify_syntax", {"text": text}))
            fun = unwrap(await s.call_tool("verify_functional", {"text": text}))
            return syn, fun


async def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "ENV"
    override = None if label == "ENV" else label
    st, gen = await gpu_call(override)
    print(f"[{label}] status.available  = {st.get('available')}")
    print(f"[{label}] status.model      = {st.get('model')}")
    print(f"[{label}] status.vram       = {st.get('vram')}")
    if not gen:
        print(f"[{label}] generate: SKIPPED (tier reported unavailable)")
        return
    print(f"[{label}] generate.model    = {gen.get('model')}")
    print(f"[{label}] generate.tok_s    = {gen.get('tokens_per_s')}  "
          f"latency_s={gen.get('latency_s')}")
    syn, fun = await verify_text(gen.get("text", ""))
    print(f"[{label}] verify_syntax     = passed={syn.get('passed')} "
          f"({syn.get('reason')})")
    print(f"[{label}] verify_functional = applicable={fun.get('applicable')} "
          f"passed={fun.get('passed')} checked={fun.get('checked')}")
    if fun.get("failures"):
        print(f"[{label}] failures          = {fun.get('failures')}")


if __name__ == "__main__":
    asyncio.run(main())
