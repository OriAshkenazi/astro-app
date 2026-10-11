import datetime as dt

import pytest

from core.canonical import BirthInput, build_chart
from core.checks import check_against_chart, split_sections
from core.tables import _clock_changes, deterministic_sections


@pytest.fixture(scope="module")
def known():
    return build_chart(BirthInput(dt.date(1987, 6, 3), dt.time(14, 45), -34.6037, -58.3816,
                                  "America/Argentina/Buenos_Aires", "Buenos Aires", name="Test Person"))[0]


@pytest.fixture(scope="module")
def unknown():
    return build_chart(BirthInput(dt.date(2001, 5, 10), None, 51.5074, -0.1278, "Europe/London", "London"))[0]


def _md(chart):
    return "".join(s.markdown + "\n" for s in deterministic_sections(chart))


@pytest.mark.parametrize("which", ["known", "unknown"])
def test_sections_pass_g2_gate(which, request):
    chart = request.getfixturevalue(which)
    md = _md(chart)
    assert list(split_sections(md)) == ["s01", "s02", "s03", "s04", "s05"]
    r = check_against_chart(md, chart)
    assert r.passed, r.errors


def test_position_table_rows(known):
    s03 = deterministic_sections(known)[2].body
    for p in known["planets"]:
        assert f"| {p['name']} | {p['sign']} | {p['dms']} | {p['house']} |" in s03


def test_cover_name_only_when_given(known, unknown):
    assert "**Name:** Test Person" in deterministic_sections(known)[0].body
    assert "**Name:**" not in deterministic_sections(unknown)[0].body


def test_unknown_time_omits_houses(unknown):
    secs = {s.id: s for s in deterministic_sections(unknown)}
    assert "| House |" not in secs["s03"].body and "omitted" in secs["s04"].body
    assert "time unknown" in secs["s02"].body and "12:00" in secs["s01"].body


def test_wheel_section_embeds_svg(known):
    s05 = deterministic_sections(known)[4]
    assert s05.data["svg"].startswith("<svg") and "Ascendant" in s05.body


def test_clock_changes_plain_language():
    trs = [{"local_before": "2021-03-28 00:59", "local_after": "2021-03-28 02:00", "dst": True, "new_utc_offset_hours": 1.0},
           {"local_before": "2021-10-31 01:59", "local_after": "2021-10-31 01:00", "dst": False, "new_utc_offset_hours": 0.0}]
    assert _clock_changes(trs) == ("On 28 March 2021 at 01:00 clocks went forward to 02:00 (summer time, UTC+1); "
                                   "on 31 October 2021 at 02:00 clocks went back to 01:00 (standard time, UTC+0).")
    assert _clock_changes([]).startswith("none")
