from astro_app.core.checks import check_against_chart, check_translation, split_sections

CHART = {
    "birth": {"lat_dms": "10°00′00″N", "lon_dms": "20°00′00″E"},
    "planets": [{"name": "Sun", "sign": "Virgo", "dms": "27°01′", "display": "Virgo 27°01′", "house": 3,
                 "deg_to_next_cusp": 2.02},
                {"name": "Pluto", "sign": "Scorpio", "dms": "23°21′", "display": "Scorpio 23°21′", "house": 4}],
    "angles": {"ASC": {"dms": "3°58′"}},
    "houses": [{"dms": "3°58′"}],
    "aspects": [{"p1": "Sun", "p2": "Pluto", "type": "sextile", "orb_dms": "3°40′"}],
}
GOOD = """<!-- s01 -->
## 1. Test
**[Calculated fact]** The Sun is at Virgo 27°01′ in house 3, 2°01′ before the next cusp.
- sextile Pluto, 3°40′;
**[Interpretive possibility]** Text.
"""


def test_sections_split():
    assert list(split_sections(GOOD + "<!-- s02 -->\nx")) == ["s01", "s02"]


def test_good_text_passes():
    r = check_against_chart(GOOD, CHART)
    assert r.passed, r.errors


def test_unknown_degree_fails():
    assert not check_against_chart(GOOD.replace("2°01′", "2°07′"), CHART).passed


def test_wrong_house_fails():
    r = check_against_chart(GOOD.replace("in house 3", "in house 4"), CHART)
    assert any("house 4" in e for e in r.errors)


def test_wrong_aspect_orb_fails():
    r = check_against_chart(GOOD.replace("sextile Pluto, 3°40′", "trine Pluto, 3°40′"), CHART)
    assert any("trine to Pluto" in e for e in r.errors)


TAGS = {"Calculated fact": "עובדה מחושבת", "Interpretive possibility": "אפשרות פרשנית"}
HE = """<!-- s01 -->
## 1. בדיקה
**[עובדה מחושבת]** השמש בבתולה 27°01′ בבית 3, 2°01′ לפני הקודקוד.
- סקסטייל לפלוטו, 3°40′;
**[אפשרות פרשנית]** טקסט.
"""


def test_translation_parity_passes():
    r = check_translation(GOOD, HE, TAGS)
    assert r.passed and not r.warnings, (r.errors, r.warnings)


def test_translation_degree_drift_fails():
    assert not check_translation(GOOD, HE.replace("3°40′", "3°04′"), TAGS).passed


def test_translation_missing_tag_fails():
    assert not check_translation(GOOD, HE.replace("**[אפשרות פרשנית]** ", ""), TAGS).passed


def test_translation_bidi_controls_fail():
    assert not check_translation(GOOD, HE.replace("השמש", "‏השמש"), TAGS).passed


def test_translation_section_ids_must_match():
    assert not check_translation(GOOD, HE.replace("s01", "s02"), TAGS).passed


def test_translation_plain_number_drift_is_warning():
    r = check_translation(GOOD, HE.replace("בבית 3", "בבית השלישי"), TAGS)
    assert r.passed and r.warnings
