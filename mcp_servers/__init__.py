"""MCP server interface for the edge cascade.

Each module here exposes one hardware/trust boundary as an MCP server the
Claude Code agent drives as the Central Architecture Router (see
../ARCHITECTURE.md). Excluded from the coverage gate (stdio loop / real
hardware / network); the regression net is test_smoke_imports.py.
"""
