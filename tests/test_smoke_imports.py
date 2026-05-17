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


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    mod = importlib.import_module(name)
    assert mod is not None


def test_entrypoints_expose_main():
    for name in ("cli", "validate_log", "vs", "lookahead", "webchat"):
        assert callable(importlib.import_module(name).main)
