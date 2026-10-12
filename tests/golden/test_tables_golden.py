"""Deterministic sections must reproduce the hand-locked reading (s01, s03, s04 byte-for-byte)."""
import datetime as dt

import pytest

from conftest import REF
from astro_app.core.canonical import BirthInput, build_chart
from astro_app.core.checks import check_against_chart, split_sections
from astro_app.core.tables import deterministic_sections


@pytest.fixture(scope="module")
def generated(golden):
    b = golden["birth"]
    chart, _ = build_chart(BirthInput(dt.date.fromisoformat(b["date_local"]), dt.time.fromisoformat(b["time_local"]),
                                      b["lat"], b["lon"], golden["time"]["tz_name"], b["place"],
                                      name=b.get("name_for_cover_only")))
    return chart, split_sections("".join(s.markdown + "\n" for s in deterministic_sections(chart)))


@pytest.fixture(scope="module")
def locked():
    p = REF / "04_interpretation" / "english_LOCKED.md"
    if not p.is_file():
        pytest.skip("locked reading not available")
    return split_sections(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("sid", ["s01", "s03", "s04"])
def test_identical_to_locked(generated, locked, sid):
    assert generated[1][sid].strip() == locked[sid].strip()


def test_prose_sections_pass_g2_and_keep_reference_facts(generated, locked):
    chart, secs = generated
    md = "".join(f"<!-- {k} -->{v}" for k, v in secs.items())
    assert check_against_chart(md, chart).passed
    for fact in ("**not** in effect", "(the previous calendar day in UTC)", "identical set of 30 aspects"):
        assert fact in secs["s02"], fact
    for fact in ("is on the left", "near the top", "retrograde markers on Saturn, Uranus and Neptune"):
        assert fact in secs["s05"] and fact.split()[-1] in locked["s05"], fact
