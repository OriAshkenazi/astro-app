import copy
import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from astro_app.core.canonical import BirthInput, build_chart
from astro_app.core.wheel import MIN_SEP_HARD, build_model, render_svg, verify

SVG = "{http://www.w3.org/2000/svg}"


@pytest.fixture(scope="module")
def chart():
    return build_chart(BirthInput(dt.date(1987, 6, 3), dt.time(14, 45), -34.6037, -58.3816,
                                  "America/Argentina/Buenos_Aires", "Buenos Aires"))[0]


def _factors(svg: str) -> set[str]:
    root = ET.fromstring(svg)
    return {g.get("data-factor") for g in root.iter(f"{SVG}g") if g.get("data-factor")}


def test_layout_passes_geometry_checks(chart):
    m = build_model(chart)
    assert verify(m) == [] and m.warnings == []


def test_svg_is_valid_xml_with_every_factor(chart):
    f = _factors(render_svg(chart))
    assert {f"planet:{p['name']}" for p in chart["planets"]} <= f
    assert {f"house:{h}" for h in range(1, 13)} <= f
    assert {"angle:ASC", "angle:MC", "angle:DSC", "angle:IC"} <= f
    drawn = {x for x in f if x.startswith("aspect:")}
    expected = {f"aspect:{a['p1']}|{a['p2']}|{a['type']}" for a in chart["aspects"] if a["type"] != "conjunction"}
    assert drawn == expected


def test_unknown_time_wheel_has_no_houses():
    c, _ = build_chart(BirthInput(dt.date(2001, 5, 10), None, 51.5074, -0.1278, "Europe/London", "London"))
    m = build_model(c)
    assert m.ref_lon == 0.0 and verify(m) == []
    f = _factors(render_svg(c))
    assert not any(x.startswith(("house:", "angle:")) for x in f)


def test_crowded_house_keeps_glyphs_apart(chart):
    c = copy.deepcopy(chart)
    h = c["houses"][2]
    for i, p in enumerate(c["planets"][:7]):  # seven planets within 3° inside house 3
        p["lon"] = (h["lon"] + 5 + i * 0.5) % 360
        p["house"] = 3
    m = build_model(c)
    ds = sorted(p["display_lon"] for p in m.planets[:7])
    assert min(b - a for a, b in zip(ds, ds[1:])) >= MIN_SEP_HARD - 1e-6
    assert all("glyphs" not in e for e in verify(m))


def test_aria_labels_are_plain_text(chart):
    root = ET.fromstring(render_svg(chart))
    labels = [g.get("aria-label") for g in root.iter(f"{SVG}g") if g.get("data-factor", "").startswith("planet:")]
    assert all(l and "<" not in l for l in labels)
