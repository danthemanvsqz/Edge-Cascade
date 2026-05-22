"""Real-tier Ops adapter: maps worker dataclasses -> mesh boundary types.

No hardware -- the worker handles are fakes; the gate uses the real (pure,
AST-only) verifier. Proves the adapter shapes match what mesh.solve expects.
"""
from __future__ import annotations

from dataclasses import dataclass

from cascade import mesh, wiring


@dataclass
class _Route:
    difficulty: float
    category: str


@dataclass
class _Draft:
    text: str


@dataclass
class _Gen:
    text: str
    available: bool = True


class _NPU:
    def route(self, _q):
        return _Route(0.62, "standard")

    def draft(self, _q):
        return _Draft("DRAFT TEXT")


class _GPU:
    def __init__(self, available=True, text="GEN TEXT"):
        self._available = available
        self._text = text

    def available(self):
        return self._available

    def generate(self, _q):
        return _Gen(self._text, available=True)


def test_build_ops_maps_route_and_draft():
    ops = wiring.build_ops(_NPU(), _GPU())
    r = ops.route("q")
    assert isinstance(r, mesh.RouteInfo) and r.difficulty == 0.62
    assert r.category == "standard"
    d = ops.draft("q")
    assert isinstance(d, mesh.Candidate) and d.text == "DRAFT TEXT" and d.available


def test_build_ops_generate_available():
    ops = wiring.build_ops(_NPU(), _GPU(available=True, text="ok"))
    c = ops.generate("q")
    assert c.available and c.text == "ok"


def test_build_ops_generate_unavailable():
    ops = wiring.build_ops(_NPU(), _GPU(available=False))
    c = ops.generate("q")
    assert not c.available and c.text == ""


def test_gate_passes_on_valid_code_block():
    g = wiring.gate("here you go:\n```python\nx = 1\n```")
    assert g.passed and g.failures == ()


def test_gate_fails_without_code_and_yields_repair_failure():
    g = wiring.gate("no code, just prose")
    assert not g.passed and len(g.failures) == 1
    assert g.reason  # carries the verifier reason


def test_repair_prompt_uses_supplied_failures():
    g = wiring.gate("prose only")  # produces a failure tuple
    prompt = wiring.repair_prompt("write x", "bad answer", g.failures)
    assert "write x" in prompt and "bad answer" in prompt


def test_repair_prompt_defaults_when_no_failures():
    prompt = wiring.repair_prompt("write x", "bad answer", ())
    assert "write x" in prompt and "bad answer" in prompt


def test_build_ops_returns_a_complete_ops_bundle():
    ops = wiring.build_ops(_NPU(), _GPU())
    # solve only calls these five; all must be present and callable.
    assert all(callable(getattr(ops, n)) for n in
               ("route", "draft", "generate", "gate", "repair_prompt"))


def test_build_ops_without_igpu_leaves_drafter_none():
    ops = wiring.build_ops(_NPU(), _GPU())
    assert ops.igpu_draft is None


def test_build_ops_with_igpu_binds_the_drafter():
    ops = wiring.build_ops(_NPU(), _GPU(), igpu=_NPU())  # _NPU has .draft
    assert callable(ops.igpu_draft)
    c = ops.igpu_draft("q")
    assert isinstance(c, mesh.Candidate) and c.text == "DRAFT TEXT"
