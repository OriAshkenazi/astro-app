"""Ported G2/G3 gates must pass every reading that was locked by hand in the reference project."""
import pytest

from conftest import REF
from core.checks import check_against_chart, check_translation

TAGS = {"Calculated fact": "עובדה מחושבת", "Traditional symbolism": "סמליות מסורתית",
        "Interpretive possibility": "אפשרות פרשנית"}
ENGLISH = ["04_interpretation/english_LOCKED.md", "04_interpretation/v2_archive/english_LOCKED_v2_1.md",
           "04_interpretation/v1_archive/english_LOCKED_v1.md"]
PAIRS = [("04_interpretation/v2_archive/english_LOCKED_v2_1.md", "06_hebrew/hebrew_text_v2_1.md"),
         ("04_interpretation/v1_archive/english_LOCKED_v1.md", "06_hebrew/hebrew_text_v1.md")]


def _read(rel):
    p = REF / rel
    if not p.is_file():
        pytest.skip(f"{rel} not available")
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ENGLISH)
def test_g2_locked_english(golden, rel):
    r = check_against_chart(_read(rel), golden)
    assert r.passed, r.errors


@pytest.mark.parametrize("src,tgt", PAIRS)
def test_g3_approved_translations(golden, src, tgt):
    r = check_translation(_read(src), _read(tgt), TAGS)
    assert r.passed, r.errors
