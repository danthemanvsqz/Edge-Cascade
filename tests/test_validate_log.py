"""Regression net for the log/DSL/repair engine (validate_log.py). Pure
logic, no hardware: log parsing, code extraction + truncation repair,
defs-only line preservation, located errors, the DSL parser, and run()."""
import pytest

import validate_log as V

LOG = """\
12:00:00 === look-ahead started ===
12:00:01 ---- QUERY: first task
12:00:02 ANSWER:
```python
a = 1
```
12:00:05 ---- QUERY: second task
12:00:06 ANSWER:
hello
"""


def test_parse_records_query_boundaries_and_final_flush():
    recs = V.parse_records(LOG)
    assert len(recs) == 2
    assert recs[0][0] == "first task" and "a = 1" in recs[0][1]
    assert recs[1][0] == "second task" and "hello" in recs[1][1]


def test_extract_code_variants():
    assert V.extract_code("```python\nx=1\n```") == "x=1"
    assert V.extract_code("no fence") is None
    # truncated (unclosed) fence -> trimmed until it compiles
    assert V.extract_code("```python\nok=1\nbad(") == "ok=1"
    # contract: FIRST fenced block (then truncation-repair) -- note this
    # differs from verifier.extract_code, which takes the LONGEST block.
    assert V.extract_code(
        "```python\nfirst=1\n```\n```python\nsecond=2\n```") == "first=1"


def test_defs_only_preserves_line_numbers():
    src = "import os\nboom_undefined\ndef f():\n    return 1\n"
    out = V._defs_only(src).splitlines()
    assert out[0] == "import os"
    assert out[1] == ""                       # crashing stmt blanked
    assert out[2] == "def f():"               # def + line number intact
    assert V._defs_only("def f(:") is None    # syntax error
    assert V._defs_only("x = 1") is None      # nothing worth keeping


def test_safe_exec_and_fmt_exc():
    assert V._safe_exec("y = 1", {}) is None
    err = V._safe_exec("def g():\n raise KeyError('E')\ng()", {})
    assert "KeyError: 'E'" in err and "in g(), line" in err


def test_parse_dsl_grammar_and_errors():
    blocks = V.parse_dsl(
        "# c\n\nwhen add\n  assert add(1,2)==3\n"
        "  assert add(0,0)==0 :: identity holds\n"
    )
    assert blocks == [("add", [("add(1,2)==3", ""),
                               ("add(0,0)==0", "identity holds")])]
    with pytest.raises(SyntaxError):
        V.parse_dsl("assert x")               # assert before when
    with pytest.raises(SyntaxError):
        V.parse_dsl("when a\n  bogus x")      # unknown keyword


def test_run_pass_fail_exception_and_defs_fallback():
    b = [("add", [("add(1,2) == 3", "sum")])]
    ok = V.run("def add(a, b):\n    return a + b\n", b)
    assert len(ok) == 1 and ok[0].ok and ok[0].sym == "add"

    bad = V.run("def add(a, b):\n    return a - b\n", b)
    assert bad[0].ok is False and "evaluated to" in bad[0].observed

    exc = V.run("def add(a, b):\n    raise ValueError('x')\n", b)
    assert exc[0].ok is False and "ValueError" in exc[0].observed

    # module-level crash + a def -> defs-only fallback still runs the check
    fb = V.run("missing_name\n\ndef add(a, b):\n    return a + b\n", b)
    assert fb[0].ok is True

    se = V.run("def f(:\n", [("f", [("f()", "")])])
    assert se[0].sym == "<exec>" and se[0].ok is False


def test_dsl_helpers():
    assert V.approx(1.0, 1.0 + 1e-12) and not V.approx(1.0, 2.0)
    assert V.sorts_like(sorted) is True
    with pytest.raises(AssertionError):
        V.sorts_like(lambda xs: xs)            # identity != sorted

    def dijkstra(g, s):
        import heapq
        dist = {n: float("inf") for n in g}
        for nbrs in g.values():
            for n in nbrs:
                dist.setdefault(n, float("inf"))
        dist[s] = 0
        pq = [(0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w in g.get(u, {}).items():
                if d + w < dist[v]:
                    dist[v] = d + w
                    heapq.heappush(pq, (dist[v], v))
        return dist
    assert V.drone_ok(dijkstra) is True
    with pytest.raises(AssertionError):
        V.drone_ok(lambda g, s: {"E": 999})


def test_is_avl_detects_imbalance():
    class Node:
        def __init__(self, k):
            self.key, self.left, self.right, self.height = k, None, None, 1

    class Tree:
        def insert(self, root, key):          # deliberately unbalanced chain
            if root is None:
                return Node(key)
            root.right = self.insert(root.right, key)
            return root

        def delete(self, root, key):
            return root

    with pytest.raises(AssertionError):
        V.is_avl(Tree)
