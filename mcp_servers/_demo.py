"""Generic MCP stdio demo harness -- proves a server speaks the protocol.

The agent can't hot-load MCP servers into a live session, so a demo is: spawn
the server as a real subprocess, do the MCP handshake over stdio, list its
tools, and invoke them -- exactly what Claude Code does once .mcp.json is wired.

Usage:
  python -m mcp_servers._demo <server_module> '<json calls>'
  json calls = [{"tool": "name", "args": {...}}, ...]   ([] = just list tools)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


def _unwrap(result) -> object:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        # FastMCP wraps non-dict returns as {"result": ...}
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    parts = []
    for c in result.content:
        parts.append(getattr(c, "text", repr(c)))
    return "".join(parts)


async def run(server_module: str, calls: list[dict]) -> None:
    # Forward the full environment: the SDK otherwise hands the server only a
    # minimal env, so env-driven config (and the .mcp.json `env` block Claude
    # Code passes) would never reach it.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", server_module], cwd=str(ROOT),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"=== {server_module} :: {len(tools)} tool(s) ===")
            for t in tools:
                summary = (t.description or "").strip().splitlines()[0]
                print(f"  - {t.name}: {summary}")
            for call in calls:
                name, args = call["tool"], call.get("args", {})
                print(f"\n>>> {name}({json.dumps(args)[:120]})")
                res = await session.call_tool(name, arguments=args)
                out = _unwrap(res)
                rendered = (
                    json.dumps(out, indent=2)
                    if isinstance(out, (dict, list))
                    else str(out)
                )
                flag = " [isError]" if getattr(res, "isError", False) else ""
                print(f"<<<{flag}\n{rendered}")


def main() -> None:
    server_module = sys.argv[1]
    calls = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
    asyncio.run(run(server_module, calls))


if __name__ == "__main__":
    main()
