import datetime as dt

import pytest

from astro_app.core.timeutil import LocalTimeError, resolve_local


def test_nonexistent_local_time_asks_user():
    with pytest.raises(LocalTimeError) as e:
        resolve_local(dt.date(2021, 3, 14), dt.time(2, 30), "America/New_York")
    assert e.value.kind == "nonexistent" and len(e.value.options) == 2


def test_ambiguous_local_time_asks_user_then_resolves_with_fold():
    with pytest.raises(LocalTimeError) as e:
        resolve_local(dt.date(2021, 11, 7), dt.time(1, 30), "America/New_York")
    assert e.value.kind == "ambiguous"
    first = resolve_local(dt.date(2021, 11, 7), dt.time(1, 30), "America/New_York", fold=0)
    second = resolve_local(dt.date(2021, 11, 7), dt.time(1, 30), "America/New_York", fold=1)
    assert (second.utc - first.utc) == dt.timedelta(hours=1)
    assert first.dst_in_effect and not second.dst_in_effect


def test_year_transitions_located_to_the_minute():
    r = resolve_local(dt.date(2021, 7, 1), dt.time(12, 0), "Europe/London")
    assert [t["utc"] for t in r.year_transitions] == ["2021-03-28T01:00Z", "2021-10-31T01:00Z"]
    assert r.dst_in_effect and r.utc_offset_hours == 1.0
