import datetime as dt
import json

from astro_app.core.canonical import BirthInput, build_chart


def test_unknown_time_noon_chart_without_houses():
    c, gate = build_chart(BirthInput(dt.date(2001, 5, 10), None, 51.5074, -0.1278, "Europe/London", "London"))
    assert gate.passed
    assert "houses" not in c and "angles" not in c and "house" not in c["planets"][0]
    assert all("ASC" not in (a["p1"], a["p2"]) and "MC" not in (a["p1"], a["p2"]) for a in c["aspects"])
    assert c["birth"]["time_known"] is False
    assert c["sensitivity_caveats"][0].startswith("Birth time unknown")


def test_southern_hemisphere():
    c, gate = build_chart(BirthInput(dt.date(1987, 6, 3), dt.time(14, 45), -34.6037, -58.3816,
                                     "America/Argentina/Buenos_Aires", "Buenos Aires"))
    assert gate.passed and c["settings"]["house_system"] == "Placidus"
    assert c["birth"]["lat_dms"].endswith("S") and c["birth"]["lon_dms"].endswith("W")


def test_polar_latitude_falls_back_to_porphyry():
    c, gate = build_chart(BirthInput(dt.date(2000, 1, 15), dt.time(10, 0), 69.6492, 18.9553,
                                     "Europe/Oslo", "Tromsø"))
    assert gate.passed, gate.issues
    assert c["settings"]["house_system"] in ("Porphyry", "Placidus")
    if c["settings"]["house_system"] == "Porphyry":
        assert "house_system_note" in c["settings"]
    assert len(c["houses"]) == 12 and sum(len(h["occupants"]) for h in c["houses"]) == 10


def test_chart_is_plain_json():
    c, _ = build_chart(BirthInput(dt.date(1987, 6, 3), dt.time(14, 45), -34.6037, -58.3816,
                                  "America/Argentina/Buenos_Aires", "Buenos Aires"))
    assert json.loads(json.dumps(c, allow_nan=False)) == c
