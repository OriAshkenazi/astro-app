"""Scripted text gates.

G2 (English vs canonical data): every degree/orb string must exist in the chart, and
explicit claims ("The Sun is at Virgo 27°01′ in house 3", "square Saturn, 4°00′") must match it.
G3 (translation vs locked English): same section IDs, same degree values and tag counts per
section, no bidi control characters. Plain-number drift is a warning, not an error, because
translators may legitimately spell numbers out ("8th" -> a word); warnings go to the reviewer.

Section format: each section starts with an HTML comment `<!-- sNN -->`; paragraphs open with a
bold tag such as **[Calculated fact]**.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .constants import PLANETS, SIGNS, dms

SECTION_RE = re.compile(r"<!--\s*(s\d+)\s*-->")
DMS_RE = re.compile(r"\d+°\d{2}′(?:\d{2}″)?")
NUM_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])")
TAG_RE = re.compile(r"\*\*\[([^\]]+)\]\*\*")
BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩]")
EN_TAGS = ("Calculated fact", "Traditional symbolism", "Interpretive possibility")
ASPECTS = "conjunction|sextile|square|trine|opposition"
POINT_NAMES = {"Ascendant": "ASC", "Midheaven": "MC", **{p: p for p in PLANETS}}


@dataclass
class CheckResult:
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str):
        self.passed = False
        self.errors.append(msg)


def split_sections(md: str) -> dict[str, str]:
    parts = SECTION_RE.split(md)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def allowed_dms(chart: dict) -> set[str]:
    s = {p["dms"] for p in chart["planets"]} | {a["orb_dms"] for a in chart["aspects"]}
    s |= {a["dms"] for a in chart.get("angles", {}).values()} | {h["dms"] for h in chart.get("houses", [])}
    s |= {chart["birth"]["lat_dms"][:-1], chart["birth"]["lon_dms"][:-1]}
    s |= {dms(p["deg_to_next_cusp"]) for p in chart["planets"] if "deg_to_next_cusp" in p}
    return s


def check_against_chart(md: str, chart: dict) -> CheckResult:
    """G2 numeric gate for an English reading."""
    r = CheckResult()
    ok = allowed_dms(chart)
    for sid, text in split_sections(md).items():
        for m in DMS_RE.findall(text):
            if m not in ok:
                r.fail(f"{sid}: {m} is not a value in the chart")
    pl = {p["name"]: p for p in chart["planets"]}
    sign_alt = "|".join(SIGNS)
    for m in re.finditer(rf"\b(?:The )?({'|'.join(PLANETS)}) is at ({sign_alt}) (\d+°\d{{2}}′)(?: in house (\d+))?", md):
        name, sign, d, house = m.groups()
        p = pl[name]
        if (sign, d) != (p["sign"], p["dms"]):
            r.fail(f"{name} stated at {sign} {d}, chart has {p['display']}")
        if house and "house" in p and int(house) != p["house"]:
            r.fail(f"{name} stated in house {house}, chart has house {p['house']}")
    pairs: dict[tuple, set] = {}
    for a in chart["aspects"]:
        for x, y in ((a["p1"], a["p2"]), (a["p2"], a["p1"])):
            pairs.setdefault((a["type"], y), set()).add(a["orb_dms"])
    for m in re.finditer(rf"\b({ASPECTS}) ({'|'.join(POINT_NAMES)}), (\d+°\d{{2}}′)", md):
        typ, partner, orb = m.groups()
        if orb not in pairs.get((typ, POINT_NAMES[partner]), set()):
            r.fail(f"'{m.group(0)}': no {typ} to {partner} with orb {orb} in the chart")
    return r


def check_translation(src: str, tgt: str, tag_map: dict[str, str] | None = None) -> CheckResult:
    """G3 parity gate: `tag_map` maps English tag text -> translated tag text."""
    r = CheckResult()
    a, b = split_sections(src), split_sections(tgt)
    if list(a) != list(b):
        r.fail(f"section IDs differ: {list(a)} vs {list(b)}")
    if BIDI_RE.search(tgt):
        r.fail(f"{len(BIDI_RE.findall(tgt))} bidi control characters in translation")
    inv = {v: k for k, v in (tag_map or {}).items()}
    for sid in a.keys() & b.keys():
        ea, eb = a[sid], b[sid]
        da, db = Counter(DMS_RE.findall(ea)), Counter(DMS_RE.findall(eb))
        if da != db:
            r.fail(f"{sid}: degree values differ (missing {dict(da - db)}, extra {dict(db - da)})")
        na = Counter(NUM_RE.findall(DMS_RE.sub(" ", ea)))
        nb = Counter(NUM_RE.findall(DMS_RE.sub(" ", eb)))
        if na != nb:
            r.warnings.append(f"{sid}: numbers differ (missing {dict(na - nb)}, extra {dict(nb - na)})")
        ta = Counter(TAG_RE.findall(ea))
        tb = Counter(inv.get(t, t) for t in TAG_RE.findall(eb))
        if ta != tb:
            r.fail(f"{sid}: paragraph tags differ ({dict(ta)} vs {dict(tb)})")
    return r
