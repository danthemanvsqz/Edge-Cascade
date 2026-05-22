"""edge-image request-spec validation.

`scripts.image_server` lives behind the opt-in `imagegen` extra (FastAPI +
diffusers), so these run on the GPU box where that extra is installed and skip
in the minimal test env. The validator is the cheap guard that rejects a
degenerate width/height *before* the GPU call (an odd or extreme dim OOMs or
silently degrades SDXL), so it's worth pinning where it actually executes.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

from pydantic import ValidationError  # noqa: E402

from scripts.image_server import Spec  # noqa: E402


def test_default_dims_pass():
    s = Spec(prompt="a cat")
    assert s.width == s.height  # both default to CONFIG.image_size
    assert s.width % 8 == 0


@pytest.mark.parametrize("dim", [512, 1024, 1536, 2048])
def test_valid_dims_accepted(dim):
    s = Spec(prompt="x", width=dim, height=dim)
    assert s.width == dim and s.height == dim


@pytest.mark.parametrize("dim", [777, 1023, 1500])  # in range, not multiples of 8
def test_non_multiple_of_8_rejected(dim):
    with pytest.raises(ValidationError):
        Spec(prompt="x", width=dim)


@pytest.mark.parametrize("dim", [256, 504, 2056, 4096])  # outside [512, 2048]
def test_out_of_range_rejected(dim):
    with pytest.raises(ValidationError):
        Spec(prompt="x", height=dim)
