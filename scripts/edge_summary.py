"""edge-cli launch-time system summary (SD-1).

Queries each wired MCP server's `.status` (or equivalent) via real MCP stdio
and prints a one-line-per-tier summary so any DEGRADED state is plain text at
launch -- not buried in a `.rec` payload that only a retrospective surfaces.

The fix for the Phase A miss (#57): NPU was `available:false` for the entire
~95-min build but every call returned `ok:true` at the MCP layer, so no
operator-visible signal existed. SD-2 (#58) closed the runtime side via the
dashboard; this closes the launch-time side.

Invoked by scripts/edge-cli.ps1 right before exec-ing into Claude. Exit code
is always 0 -- diagnostic, never blocks launch.

Run manually:  uv run python scripts/edge_summary.py runs/edge-local.mcp.json
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError as _imp_err:  # pragma: no cover -- venv missing `mcp` extra
    # The launch-time summary is a *diagnostic*; never block launch when the
    # `mcp` extra is missing (e.g. -SkipSync against a stale venv). Print a
    # one-line fallback and exit 0 so edge-cli.ps1 proceeds.
    print(f"    summary unavailable: {_imp_err} -- run `uv sync --extra mcp`")
    raise SystemExit(0) from None

TIMEOUT_INIT = 10.0          # MCP handshake; should be sub-second when healthy
TIMEOUT_TOOL = 30.0          # NPU compile is ~9s on first call; pad for variance

# edge-cloud uses `budget` as its readiness-check tool (not `status`).
STATUS_TOOL: dict[str, str] = {"edge-cloud": "budget"}

# Servers we know how to interpret. Anything else in the config is rendered
# generically (so a future tier addition is one line, not a regression).
KNOWN: tuple[str, ...] = ("edge-npu", "edge-gpu", "edge-verify", "edge-cloud")


def _params(spec: dict) -> StdioServerParameters:
    return StdioServerParameters(
        command=spec["command"],
        args=list(spec.get("args", [])),
        env=dict(spec["env"]) if spec.get("env") else None,
        cwd=spec.get("cwd"),
    )


def _payload(res) -> dict:
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        return sc.get("result", sc)
    try:
        return json.loads("".join(getattr(c, "text", "") for c in res.content))
    except (ValueError, TypeError):
        return {}


def _summarize_npu(p: dict) -> tuple[bool, str]:
    if not p.get("available"):
        reason = (p.get("reason") or "available:false").strip().replace("\n", " ")
        # Trim wide enough that the openvino_genai install hint stays visible.
        return False, f"available:false -- {reason[:140]}"
    return True, f"device={p.get('device', '?')} max_tokens={p.get('npu_max_tokens', '?')}"


def _summarize_gpu(p: dict) -> tuple[bool, str]:
    if not p.get("available"):
        return False, "available:false (Ollama not up / model not pulled)"
    return True, f"{p.get('model', '?')} via Ollama"


def _summarize_cloud(p: dict) -> tuple[bool, str]:
    allowed = bool(p.get("allowed"))
    spent = p.get("usd_spent", 0)
    cap = p.get("usd_budget", 0)
    state = "allowed" if allowed else "budget-locked"
    return allowed, f"{state} usd=${spent}/${cap}"


SUMMARIZE = {
    "edge-npu": _summarize_npu,
    "edge-gpu": _summarize_gpu,
    "edge-cloud": _summarize_cloud,
}


async def _query(name: str, spec: dict) -> tuple[str, str]:
    """Return (state, summary). state ∈ {READY, DEGRADED, ERROR}."""
    # MCP servers stream INFO-level chatter to stderr (FastMCP request logs,
    # ollama HTTP calls, NPU compile output). Silence it -- the summary's job
    # is to render ONE line per tier; the .rec files are the durable record.
    errlog = open(os.devnull, "w", encoding="utf-8")
    try:
        async with stdio_client(_params(spec), errlog=errlog) as (r, w):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), timeout=TIMEOUT_INIT)
                # edge-verify is a deterministic gate with no status tool --
                # successful initialize() + non-empty list_tools() IS the
                # ready signal (the Python sandbox is in-process; if the
                # module imported, the gate is up).
                if name == "edge-verify":
                    tools = await asyncio.wait_for(s.list_tools(), timeout=TIMEOUT_INIT)
                    return "READY", f"deterministic sandbox up ({len(tools.tools)} tools)"
                tool = STATUS_TOOL.get(name, "status")
                res = await asyncio.wait_for(s.call_tool(tool, {}), timeout=TIMEOUT_TOOL)
                payload = _payload(res)
                fmt = SUMMARIZE.get(name)
                if fmt is None:
                    avail = bool(payload.get("available", True))
                    return ("READY" if avail else "DEGRADED"), json.dumps(payload)[:90]
                ok, summary = fmt(payload)
                return ("READY" if ok else "DEGRADED"), summary
    except TimeoutError:
        return "DEGRADED", f"timeout (>{TIMEOUT_TOOL:.0f}s) -- server unresponsive"
    except Exception as e:  # noqa: BLE001 -- launch-time diagnostic, never raises
        return "ERROR", f"{type(e).__name__}: {str(e)[:90]}"
    finally:
        errlog.close()


async def build_rows(servers: dict[str, dict]) -> list[tuple[str, str, str]]:
    """Query each wired tier and produce one (name, state, summary) row per
    server. KNOWN tiers come first in canonical order; unknown extras follow
    so a forward-added server in the config is still visible, just generic."""
    rows: list[tuple[str, str, str]] = []
    for name in KNOWN:
        if name not in servers:
            note = "(-WithCloud to enable)" if name == "edge-cloud" else "(not in this config)"
            rows.append((name, "NOT WIRED", note))
            continue
        state, summary = await _query(name, servers[name])
        rows.append((name, state, summary))
    for name in servers:
        if name not in KNOWN:
            state, summary = await _query(name, servers[name])
            rows.append((name, state, summary))
    return rows


def format_rows(rows: list[tuple[str, str, str]]) -> list[str]:
    """Pad name + bracketed-state columns to their widest entries so all rows
    line up regardless of what each tier reported. Empty input is allowed --
    `build_rows` always emits the four KNOWN tiers today, but a future
    refactor that lets the list be empty must not crash the launcher."""
    if not rows:
        return []
    name_w = max(len(r[0]) for r in rows)
    state_w = max(len(f"[{r[1]}]") for r in rows)
    return [
        f"    {name.ljust(name_w)}  {f'[{state}]'.ljust(state_w)}  {summary}"
        for name, state, summary in rows
    ]


async def main(config_path: Path) -> int:  # pragma: no cover -- launcher glue
    servers = json.loads(config_path.read_text(encoding="utf-8")).get("mcpServers", {})
    for line in format_rows(await build_rows(servers)):
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover -- launcher glue
    if len(sys.argv) != 2:
        print("usage: edge_summary.py <mcp-config.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
