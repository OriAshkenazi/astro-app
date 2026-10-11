"""The automated pipeline must reproduce a hand-gated reference chart.

Inputs and expectations are read from the private reference dataset (see conftest);
nothing personal is stored in this repository.
"""
import datetime as dt
import re

import pytest

from core.canonical import BirthInput, build_chart


@pytest.fixture(scope="module")
def built(golden):
    b, t = golden["birth"], golden["time"]
    inp = BirthInput(date=dt.date.fromisoformat(b["date_local"]), time=dt.time.fromisoformat(b["time_local"]),
                     lat=b["lat"], lon=b["lon"], tz_name=t["tz_name"], place=b["place"])
    return build_chart(inp)


def test_gate_passes(built):
    _, gate = built
    assert gate.passed, gate.issues
    assert gate.max_planet_diff_arcsec < 2.0
    assert gate.max_angle_diff_deg < 0.01


@pytest.mark.parametrize("key", ["planets", "angles", "houses", "aspects", "aspect_patterns", "elements",
                                 "modalities", "angle_signs", "intercepted_signs", "derived"])
def test_computed_sections_identical(built, golden, key):
    assert built[0][key] == golden[key]


def test_time_block(built, golden):
    t, g = built[0]["time"], golden["time"]
    for k in ("tz_name", "utc_offset_hours", "dst_in_effect", "utc_datetime", "jd_ut", "jd_tt",
              "delta_t_seconds", "gast_hours", "ramc_deg", "true_obliquity_deg"):
        assert t[k] == g[k], k


def test_birth_coords(built, golden):
    b, g = built[0]["birth"], golden["birth"]
    for k in ("date_local", "time_local", "lat", "lon", "lat_dms", "lon_dms"):
        assert b[k] == g[k], k


def test_intercepted_note_covers_reference(built, golden):
    note = built[0]["intercepted_note"] or ""
    for s in golden["intercepted_signs"]:
        assert f"{s} is wholly contained in house" in note


def test_generated_caveats_cover_hand_written(built, golden):
    """Every factor named in the hand-written caveats is flagged by the automated scan."""
    s = built[0]["sensitivity"]
    hand = " ".join(golden["sensitivity_caveats"])
    clauses = [c for c in re.split(r"[.;]\s", hand)]
    for name in (p["name"] for p in golden["planets"]):
        if any(re.match(rf"{name}\b(?!–)", c) and re.search(r"house \d|cusp", c) for c in clauses):
            r = s["planet_house_change_minutes"][name]
            assert min(x for x in (r["earlier"], r["later"], 99) if x is not None) <= 10, name
    for a, b in re.findall(r"Cusps (\d+) and (\d+)", hand):
        for h in (a, b):
            r = s["cusp_sign_change_minutes"][h]
            assert min(x for x in (r["earlier"], r["later"], 99) if x is not None) <= 10, h
    flagged = {frozenset((x["p1"], x["p2"])) for x in s["weak_aspects"] + s["angle_aspect_dropout_minutes"]}
    weak_text = " ".join(c for c in golden["sensitivity_caveats"] if "weak" in c)
    for p1, p2 in re.findall(r"(\w+)–(\w+) (?:conjunction|sextile|square|trine|opposition)", weak_text):
        assert frozenset((p1, p2)) in flagged, (p1, p2)
