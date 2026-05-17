"""Regression smoke test for the edge-npu MCP-stdio hang.

History: openvino_genai.LLMPipeline compile returns in ~3.5s in a normal
process but NEVER returns inside the FastMCP request-dispatch path (measured,
process-global). Fix: OpenVINO runs in an isolated worker subprocess
(mcp_servers/_npu_worker_proc.py) over a private pipe.

This test pins that fix: it drives edge-npu over real MCP stdio and asserts
`status` RETURNS (device available) within a hard bound. Pre-fix it FAILS
(timeout); post-fix it PASSES (~4s). LOCAL ONLY -- needs OpenVINO + Intel
hardware; SKIPs cleanly otherwise so it never blocks CI / a push.

Exit: 0 = passed or cleanly skipped; 1 = the hang regressed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS_BOUND = 60  # pre-fix this blew 60s; post-fix ~4s


def _skip(msg: str) -> None:
    print(f"SKIP: {msg}")
    sys.exit(0)


async def _run() -> int:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ModuleNotFoundError:
        _skip("mcp extra not installed")
    try:
        import openvino_genai  # noqa: F401
    except ModuleNotFoundError:
        _skip("openvino_genai not installed (uv sync --extra accel)")

    env = dict(os.environ)
    env.setdefault("CASCADE_SKIP_NPU", "1")  # bounded iGPU Tier-1 path
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_servers.npu"],
        cwd=str(ROOT), env=env,
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            try:
                res = await asyncio.wait_for(
                    s.call_tool("status", {}), timeout=STATUS_BOUND)
            except TimeoutError:
                print(f"FAIL: edge-npu status() hung > {STATUS_BOUND}s "
                      f"(the MCP-stdio hang regressed)")
                return 1
    sc = getattr(res, "structuredContent", None)
    out = sc.get("result", sc) if isinstance(sc, dict) else None
    if out is None:
        out = json.loads("".join(c.text for c in res.content))
    if not out.get("available"):
        print(f"FAIL: edge-npu not available: {out.get('reason')}")
        return 1
    print(f"PASS: edge-npu status() returned, device={out['device']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
