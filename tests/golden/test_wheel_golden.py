"""The SVG wheel must place glyphs exactly where the reference (matplotlib) renderer did."""
import sys

import pytest

from conftest import REF
from core.wheel import build_model, verify


def test_layout_matches_reference_renderer(golden):
    sys.path.insert(0, str(REF / "05_design"))
    ref = pytest.importorskip("wheel")
    if not hasattr(ref, "Wheel"):
        pytest.skip("reference wheel module unavailable")
    expected = ref.Wheel(golden).layout_glyphs([p["lon"] for p in golden["planets"]])
    m = build_model(golden)
    assert verify(m) == []
    for e, p in zip(expected, m.planets):
        assert abs(e - p["display_lon"]) < 1e-9, p["name"]
