"""Root conftest: presence here puts the project root on sys.path so the
top-level entrypoint scripts (cli.py, validate_log.py, vs.py, lookahead.py,
webchat.py) are importable in tests, alongside the installed `cascade` pkg.
"""
