"""Cheapest, broadest regression net: every module must import cleanly.

Catches syntax errors, broken refactors, and bad imports across the modules
that are excluded from the coverage gate (hardware/CLI/server). None of these
construct workers or open sockets at import time.
"""
import importlib

import pytest

MODULES = [
    "cascade.config", "cascade.feedback", "cascade.verifier",
    "cascade.cloud_worker", "cascade.npu_worker", "cascade.gpu_worker",
    "cascade.orchestrator", "cascade.lookahead",
    "cli", "validate_log", "vs", "lookahead", "webchat",
]


# The MCP server package is behind the optional `mcp` extra (uv sync
# --extra mcp). Skip cleanly when it isn't installed rather than hard-fail.
MCP_MODULES = [
    "mcp_servers", "mcp_servers.verify", "mcp_servers._funcverify_child",
    "mcp_servers._demo", "mcp_servers.cloud", "mcp_servers.gpu",
    "mcp_servers.npu", "mcp_servers._rec",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    mod = importlib.import_module(name)
    assert mod is not None


@pytest.mark.parametrize("name", MCP_MODULES)
def test_mcp_server_modules_import(name):
    pytest.importorskip("mcp")
    mod = importlib.import_module(name)
    assert mod is not None


def test_entrypoints_expose_main():
    for name in ("cli", "validate_log", "vs", "lookahead", "webchat"):
        assert callable(importlib.import_module(name).main)
