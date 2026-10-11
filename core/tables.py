"""Deterministic reading sections s01–s05, generated from the canonical chart (no LLM).

s01 cover, s02 birth data & methodology, s03 planetary positions, s04 house cusps,
s05 wheel (caption + SVG). Output uses the reading's markdown section format
(`<!-- sNN -->` + `## N. Title`), so the G2/G3 text gates apply unchanged.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .constants import MODERN_RULER, SIGNS, TRAD_RULER, house_of
from .wheel import render_svg

TIME_SENSITIVE_MIN = 2.0  # a factor that changes within this many minutes is flagged in the tables
WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


@dataclass
class Section:
    id: str
    number: int
    title: str
    body: str
    data: dict = field(default_factory=dict)

    @property
    def markdown(self) -> str:
        return f"<!-- {self.id} -->\n## {self.number}. {self.title}\n\n{self.body.strip()}\n"


def _date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _offset(h: float) -> str:
    sign = "+" if h >= 0 else "-"
    hh, mm = divmod(round(abs(h) * 60), 60)
    return f"UTC{sign}{hh}" + (f":{mm:02d}" if mm else "")


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _sensitive_planets(chart: dict) -> list[str]:
    s = chart.get("sensitivity", {}).get("planet_house_change_minutes", {})
    return [n for n, r in s.items() if any(r[k] is not None and r[k] <= TIME_SENSITIVE_MIN for k in ("earlier", "later"))]


def _cusps_sensitive(chart: dict) -> bool:
    s = chart.get("sensitivity", {}).get("cusp_sign_change_minutes", {})
    return any(r[k] is not None and r[k] <= TIME_SENSITIVE_MIN for r in s.values() for k in ("earlier", "later"))


def _clock_changes(trs: list[dict]) -> str:
    """'On 28 March 2021 at 01:00 clocks went forward to 02:00 (summer time, UTC+1).'"""
    if not trs:
        return "none; the same UTC offset applied all year."
    out = []
    for tr in trs:
        before = dt.datetime.strptime(tr["local_before"], "%Y-%m-%d %H:%M") + dt.timedelta(minutes=1)
        after = dt.datetime.strptime(tr["local_after"], "%Y-%m-%d %H:%M")
        way = "forward" if after > before else "back"
        to = f"{after:%H:%M}" + ("" if after.date() == before.date() else f" on {_date(after.date().isoformat())}")
        kind = "summer time" if tr["dst"] else "standard time"
        out.append(f"on {_date(before.date().isoformat())} at {before:%H:%M} clocks went {way} to {to} "
                   f"({kind}, {_offset(tr['new_utc_offset_hours'])})")
    return _cap("; ".join(out)) + "."


def s01_cover(chart: dict) -> Section:
    b, t = chart["birth"], chart["time"]
    lines = ["**Personal Natal Chart**", "", "*Astrological Chart Analysis*", ""]
    if b.get("name_for_cover_only"):
        lines.append(f"- **Name:** {b['name_for_cover_only']}")
    lines.append(f"- **Date of birth:** {_date(b['date_local'])}")
    if b["time_known"]:
        lines.append(f"- **Time of birth:** {b['time_local']} (local time, {t['zone_abbrev']}, {_offset(t['utc_offset_hours'])})")
    else:
        lines.append("- **Time of birth:** unknown (positions calculated for 12:00 local time)")
    lines.append(f"- **Place of birth:** {b['place']}")
    return Section("s01", 1, "Cover information", "\n".join(lines))


def s02_method(chart: dict) -> Section:
    b, t, meta = chart["birth"], chart["time"], chart["_meta"]
    known = b["time_known"]
    when = f"{_date(b['date_local'])}, {b['time_local']}" if known else f"{_date(b['date_local'])}, time unknown"
    out = ["### Inputs", "", f"- **Local date and time:** {when}", f"- **Place:** {b['place']}",
           f"- **Coordinates:** {b['lat_dms']}, {b['lon_dms']} ({abs(b['lat']):.4f} {'N' if b['lat'] >= 0 else 'S'}, "
           f"{abs(b['lon']):.4f} {'E' if b['lon'] >= 0 else 'W'}).", "", "### Time-zone verification", "",
           f"- **Zone:** {t['tz_name']}.",
           f"- **Clock changes in {b['date_local'][:4]}:** {_clock_changes(t['year_transitions'])}"]
    utc = dt.datetime.strptime(t["utc_datetime"], "%Y-%m-%dT%H:%M:%SZ")
    dst = "daylight saving **was** in effect" if t["dst_in_effect"] else "daylight saving was **not** in effect"
    out.append(f"- **Result:** {dst}. The applicable offset is **{_offset(t['utc_offset_hours'])} ({t['zone_abbrev']})**.")
    day_note = ""
    if utc.date().isoformat() != b["date_local"]:
        day_note = " (the previous calendar day in UTC)" if utc.date().isoformat() < b["date_local"] else " (the next calendar day in UTC)"
    out.append(f"- **Universal Time{' of birth' if known else ' used'}:** **{utc:%H:%M} UTC on {_date(utc.date().isoformat())}**{day_note}.")
    out.append(f"- **Source for the rule:** {'; '.join(t['sources'])}.")
    g = meta["gate"]
    n = len(chart["aspects"])
    s = chart["settings"]
    out += ["", "### Astrological settings", "", f"- **Zodiac:** {s['zodiac']}.",
            f"- **House system:** {s['house_system']}." if known else "- **House system:** none (birth time unknown).",
            f"- **Positions:** {s['positions']}."]
    if s.get("house_system_note"):
        out.append(f"- **Note:** {s['house_system_note']}")
    out.append(f"- **Engines:** {meta['value_source']} supplied the values. {meta['confirmed_by']} "
               f"independently confirmed them. The two agree to within {g['max_planet_diff_arcsec']:.1f}″ for planets"
               + (f" and {g['max_angle_diff_deg']:.3f}° for angles and cusps" if known else "")
               + f", with an identical set of {n} aspects.")
    p = chart["aspect_policy"]
    orbs = p["orbs"]
    bodies = "the ten planets (Sun to Pluto) and the Ascendant and Midheaven" if known else "the ten planets (Sun to Pluto)"
    out += ["", "### Orb policy", "",
            f"Aspects were calculated between {bodies}. Maximum orbs: conjunction {orbs['conjunction']}°, "
            f"opposition {orbs['opposition']}°, trine {orbs['trine']}°, square {orbs['square']}°, sextile {orbs['sextile']}°, "
            "with an extra 2° when the Sun or Moon is involved. An aspect is labelled *applying* when the orb is "
            "decreasing under the planets' instantaneous speeds" + (" (the angles are held fixed for this test)" if known else "")
            + ", and *separating* otherwise. Element and modality counts use the ten planets, unweighted."
            + (" The angles are reported separately." if known else "")]
    out += ["", "### Sensitivity caveats in plain language", ""]
    out += [f"- {c}" for c in chart["sensitivity_caveats"]]
    return Section("s02", 2, "Birth data and calculation methodology", "\n".join(out))


def s03_positions(chart: dict) -> Section:
    known = chart["birth"]["time_known"]
    rows = (["| Planet | Sign | Degree | House | Retrograde |", "|---|---|---|---|---|"] if known
            else ["| Planet | Sign | Degree | Retrograde |", "|---|---|---|---|"])
    for p in chart["planets"]:
        r = "R" if p["retrograde"] else "—"
        rows.append(f"| {p['name']} | {p['sign']} | {p['dms']} | {p['house']} | {r} |" if known
                    else f"| {p['name']} | {p['sign']} | {p['dms']} | {r} |")
    tail = ""
    if known:
        for k in ("ASC", "MC"):
            a = chart["angles"][k]
            rows.append(f"| {k} | {a['sign']} | {a['dms']} | — | — |")
        dsc, ic = chart["angles"]["DSC"], chart["angles"]["IC"]
        tail = f"\n\nThe Descendant is {dsc['display']} and the IC is {ic['display']}."
        sens = _sensitive_planets(chart)
        if sens:
            tail += (f" The house placement of {sens[0]} is" if len(sens) == 1 else
                     f" The house placements of {_and(sens)} are") + " time-sensitive (see section 2)."
    else:
        tail = "\n\nThe birth time is unknown, so houses and angles are not listed (see section 2)."
    return Section("s03", 3, "Planetary-position table", "\n".join(rows) + tail,
                   data={"planets": chart["planets"], "angles": chart.get("angles")})


def s04_houses(chart: dict) -> Section | None:
    if not chart["birth"]["time_known"]:
        return Section("s04", 4, "House-cusp table", "Houses require a known birth time, so this table is omitted.")
    rows = ["| House | Sign | Degree | Occupants |", "|---|---|---|---|"]
    for h in chart["houses"]:
        rows.append(f"| {h['house']} | {h['sign']} | {h['dms']} | {', '.join(h['occupants']) or '—'} |")
    body = "\n".join(rows)
    inter = chart["intercepted_signs"]
    if inter:
        cusps = [h["lon"] for h in chart["houses"]]
        where = [(s, house_of(SIGNS.index(s) * 30 + 15, cusps)) for s in inter]
        parts = [f"{s} is entirely contained within house {h} ({chart['houses'][h - 1]['sign']} cusp)" for s, h in where]
        flag = " (time-sensitive; see section 2)" if _cusps_sensitive(chart) else ""
        body += f"\n\n**Intercepted signs{flag}:** " + _cap(", and ".join(parts) if len(parts) == 2 else _and(parts)) + "."
        body += (" Their rulers act as secondary rulers of those houses." if len(parts) > 1
                 else " Its ruler acts as a secondary ruler of that house.")
        for s, h in where:
            t, m = TRAD_RULER[s], MODERN_RULER[s]
            body += (f" {t} (ruler of {s}) is a secondary ruler of house {h}." if t == m else
                     f" {t} (traditional) or {m} (modern), the rulers of {s}, are secondary rulers of house {h}.")
    return Section("s04", 4, "House-cusp table", body, data={"houses": chart["houses"]})


def _mc_where(chart: dict) -> str:
    d = (180.0 + chart["angles"]["MC"]["lon"] - chart["angles"]["ASC"]["lon"]) % 360 - 90  # screen angle vs top
    return "near the top" if abs(d) <= 20 else "left of the top" if d > 0 else "right of the top"


def s05_wheel(chart: dict) -> Section:
    b, t = chart["birth"], chart["time"]
    utc = dt.datetime.strptime(t["utc_datetime"], "%Y-%m-%dT%H:%M:%SZ")
    pl = chart["planets"]
    if b["time_known"]:
        txt = (f"The wheel shows the tropical zodiac with {chart['settings']['house_system']} house cusps calculated for "
               f"{b['time_local']} {t['zone_abbrev']} ({utc:%H:%M} UTC on {_date(utc.date().isoformat())}) at "
               f"{b['lat_dms']}, {b['lon_dms']}. The Ascendant, {chart['angles']['ASC']['display']}, is on the left. "
               f"The Midheaven, {chart['angles']['MC']['display']}, is {_mc_where(chart)}.")
    else:
        txt = ("The birth time is unknown, so the wheel shows planets only, for 12:00 local time, with 0° Aries on the "
               "left; no houses or angles are drawn.")
    retro = [p["name"] for p in pl if p["retrograde"]]
    txt += (f" The ten planets are drawn at their positions, with retrograde markers on {_and(retro)}." if retro
            else " The ten planets are drawn at their positions; none is retrograde.")
    if b["time_known"]:
        below = chart["derived"]["hemispheres"]["below_horizon_houses_1_6"]
        above = chart["derived"]["hemispheres"]["above_horizon_houses_7_12"]
        nb = len(below)
        if nb == 10:
            txt += " All ten planets are in the lower half of the wheel, below the horizon."
        elif nb == 0:
            txt += " All ten planets are in the upper half of the wheel, above the horizon."
        else:
            txt += f" {_cap(WORDS[nb])} of the ten planets {'is' if nb == 1 else 'are'} below the horizon"
            txt += f" and {WORDS[len(above)]} above it." if len(above) > 1 else f"; {above[0]} is the only planet above it."
        groups = [f"{_and(h['occupants'])} in house {h['house']}" for h in chart["houses"] if len(h["occupants"]) >= 2]
        if groups:
            txt += f" Groups of planets: {'; '.join(groups)}."
    txt += " Aspect lines connect the planets according to the orb policy in section 2; conjunctions are not drawn as lines."
    if b["time_known"] and chart["intercepted_signs"]:
        txt += (f" {_and(chart['intercepted_signs'])} {'is' if len(chart['intercepted_signs']) == 1 else 'are'} "
                "intercepted (see section 4).")
    return Section("s05", 5, "Natal-chart wheel", txt, data={"svg": render_svg(chart)})


def deterministic_sections(chart: dict) -> list[Section]:
    return [s01_cover(chart), s02_method(chart), s03_positions(chart), s04_houses(chart), s05_wheel(chart)]
