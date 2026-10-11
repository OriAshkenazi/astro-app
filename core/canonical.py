"""Gate G1: run both engines, compare within tolerance, build the canonical chart dataset.

Generic port of the reference project's 03_canonical/build_canonical.py + 02_audit/compare.py.
Numeric values come from the Skyfield engine; Swiss Ephemeris confirms them. Derived facts
(rulers, patterns, dignities, emphasis) are computed here so no LLM agent ever invents them.
"""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field

from . import engine_skyfield as SKY
from . import engine_swe as SWE
from . import sensitivity as SENS
from .constants import (DOMICILE, ELEMENT, EXALTATION, MODALITY, MODERN_RULER, NEAR_CUSP_DEG, NEAR_ORB_LIMIT_DEG,
                        PLANETS, SIGNS, TOL_ANGLE_DEG, TOL_PLANET_DEG, TRAD_RULER, coord_dms, fmt_point, house_of,
                        orb_dms, sign_of, signed_diff)
from .timeutil import describe_transitions, resolve_local

SCHEMA_VERSION = "1.0"


@dataclass
class BirthInput:
    date: dt.date
    time: dt.time | None          # None = unknown birth time (noon chart, no houses/angles)
    lat: float
    lon: float
    tz_name: str
    place: str
    name: str | None = None
    fold: int | None = None       # disambiguates a DST-edge local time (see timeutil.LocalTimeError)


@dataclass
class GateReport:
    passed: bool
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    max_planet_diff_arcsec: float = 0.0
    max_angle_diff_deg: float = 0.0


class GateFailed(RuntimeError):
    def __init__(self, report: GateReport):
        self.report = report
        super().__init__("; ".join(report.issues))


def compare(sky: dict, swe: dict) -> GateReport:
    r = GateReport(passed=True)
    sp = {p["name"]: p for p in swe["planets"]}
    for p in sky["planets"]:
        q = sp[p["name"]]
        d = abs(signed_diff(p["lon"], q["lon"]))
        r.max_planet_diff_arcsec = max(r.max_planet_diff_arcsec, d * 3600)
        if d > TOL_PLANET_DEG:
            r.issues.append(f"{p['name']} longitude differs by {d:.4f}°")
        if p["retrograde"] != q["retrograde"] and min(abs(p["speed"]), abs(q["speed"])) > 1e-3:
            r.issues.append(f"{p['name']} retrograde flag differs")
        if "house" in p and p["house"] != q["house"]:
            gap = min(abs(signed_diff(p["lon"], c)) for c in sky["cusps"])
            (r.notes if gap < TOL_ANGLE_DEG else r.issues).append(
                f"{p['name']} house {p['house']} vs {q['house']} ({gap:.4f}° from a cusp)")
    if sky["angles"]:
        pairs = [(sky["angles"][k], swe["angles"][k]) for k in ("ASC", "MC")] + list(zip(sky["cusps"], swe["cusps"]))
        for a, b in pairs:
            r.max_angle_diff_deg = max(r.max_angle_diff_deg, abs(signed_diff(a, b)))
        if r.max_angle_diff_deg > TOL_ANGLE_DEG:
            r.issues.append(f"angle/cusp difference {r.max_angle_diff_deg:.4f}° exceeds {TOL_ANGLE_DEG}°")
    key = lambda x: (frozenset((x["p1"], x["p2"])), x["type"])
    ka, kb = {key(x): x for x in sky["aspects"]}, {key(x): x for x in swe["aspects"]}
    for k in set(ka) ^ set(kb):
        x = ka.get(k) or kb.get(k)
        if x["margin_to_limit"] < 0.05:
            r.notes.append(f"{'-'.join(sorted(k[0]))} {k[1]} at orb limit in one engine only")
        else:
            r.issues.append(f"{'-'.join(sorted(k[0]))} {k[1]} found by one engine only")
    r.passed = not r.issues
    return r


def _patterns(aspects: list[dict], planets: list[dict], bodies: list[str]) -> list[dict]:
    def has(a, b, t):
        return any({x["p1"], x["p2"]} == {a, b} and x["type"] == t for x in aspects)
    out = []
    for a, b in itertools.combinations(bodies, 2):
        if has(a, b, "opposition"):
            for c in bodies:
                if c not in (a, b) and has(a, c, "square") and has(b, c, "square"):
                    out.append({"type": "T-square", "opposition": [a, b], "apex": c})
    for a, b, c in itertools.combinations(bodies, 3):
        if has(a, b, "trine") and has(b, c, "trine") and has(a, c, "trine"):
            out.append({"type": "Grand trine", "members": [a, b, c]})
    for key in ("sign", "house"):
        if key not in planets[0]:
            continue
        groups: dict = {}
        for p in planets:
            groups.setdefault(p[key], []).append(p["name"])
        for k, v in groups.items():
            if len(v) >= 3:
                out.append({"type": f"Stellium by {key}", key: k, "members": v})
    return out


def _dignity(pl: dict) -> dict:
    opp = lambda x: SIGNS[(SIGNS.index(x) + 6) % 12]
    out = {}
    for n, signs in DOMICILE.items():
        s = pl[n]["sign"]
        if s in signs:
            out[n] = "domicile"
        elif s == EXALTATION[n]:
            out[n] = "exaltation"
        elif s in [opp(x) for x in signs]:
            out[n] = "detriment"
        elif s == opp(EXALTATION[n]):
            out[n] = "fall"
        else:
            out[n] = "peregrine (no essential dignity by sign)"
    return out


def _intercepted_note(intercepted: list[str], houses: list[dict]) -> str | None:
    if not intercepted:
        return None
    parts = []
    for s in intercepted:
        start = SIGNS.index(s) * 30.0
        h = house_of(start + 15.0, [x["lon"] for x in houses])
        trad, mod = TRAD_RULER[s], MODERN_RULER[s]
        ruler = trad if trad == mod else f"{trad} traditional / {mod} modern"
        parts.append(f"{s} is wholly contained in house {h} ({houses[h - 1]['sign']} cusp); its ruler "
                     f"({ruler}) acts as a secondary ruler of house {h}.")
    return " ".join(parts)


def build_chart(b: BirthInput) -> tuple[dict, GateReport]:
    time_known = b.time is not None
    rt = resolve_local(b.date, b.time or dt.time(12, 0), b.tz_name, b.fold)
    sky = SKY.compute(rt.utc, b.lat, b.lon, time_known)
    swe = SWE.compute(rt.utc, b.lat, b.lon, time_known, sky["house_system"] or "placidus")
    gate = compare(sky, swe)
    if not gate.passed:
        raise GateFailed(gate)

    cusps = [round(c, 6) for c in sky["cusps"]] if time_known else None
    planets = []
    for p in sky["planets"]:
        rec = {"name": p["name"], **fmt_point(p["lon"]), "speed": p["speed"], "retrograde": p["retrograde"]}
        if time_known:
            h = house_of(p["lon"], cusps)
            to_next = (cusps[h % 12] - p["lon"]) % 360
            rec.update({"house": h, "deg_to_next_cusp": round(to_next, 4), "near_next_cusp": to_next < NEAR_CUSP_DEG})
        rec.update({"element": ELEMENT[rec["sign"]], "modality": MODALITY[rec["sign"]]})
        rec["display"] += " R" if p["retrograde"] else ""
        planets.append(rec)
    pl = {p["name"]: p for p in planets}

    aspects = [{"p1": x["p1"], "p2": x["p2"], "type": x["type"], "angle": x["angle"], "orb": round(x["orb_exact"], 2),
                "orb_exact": x["orb_exact"], "orb_dms": orb_dms(x["orb_exact"]), "applying": x["applying"],
                "orb_limit": x["orb_limit"], "margin_to_limit": x["margin_to_limit"],
                "near_orb_limit": x["margin_to_limit"] < NEAR_ORB_LIMIT_DEG} for x in sky["aspects"]]
    aspects.sort(key=lambda a: a["orb_exact"])

    elements = {e: {"count": 0, "planets": []} for e in ["Fire", "Earth", "Air", "Water"]}
    modalities = {m: {"count": 0, "planets": []} for m in ["Cardinal", "Fixed", "Mutable"]}
    for p in planets:
        elements[p["element"]]["count"] += 1
        elements[p["element"]]["planets"].append(p["name"])
        modalities[p["modality"]]["count"] += 1
        modalities[p["modality"]]["planets"].append(p["name"])

    jd_swe = SWE.jd_from_utc(rt.utc)
    moon = SENS.moon_ingress(jd_swe)
    chart: dict = {
        "_meta": {"schema_version": SCHEMA_VERSION, "status": "APPROVED — Gate G1 (automated)",
                  "approved_on": dt.date.today().isoformat(),
                  "value_source": sky["engine"], "confirmed_by": swe["engine"],
                  "gate": {"max_planet_diff_arcsec": round(gate.max_planet_diff_arcsec, 2),
                           "max_angle_diff_deg": round(gate.max_angle_diff_deg, 4), "notes": gate.notes},
                  "rule": "Interpretation, design and translation agents must quote these values verbatim and may not alter them."},
        "birth": {"name_for_cover_only": b.name, "date_local": b.date.isoformat(),
                  "time_local": b.time.strftime("%H:%M") if time_known else None, "time_known": time_known,
                  "place": b.place, "lat": b.lat, "lon": b.lon,
                  "lat_dms": coord_dms(b.lat, "N", "S"), "lon_dms": coord_dms(b.lon, "E", "W")},
        "time": {"tz_name": rt.tz_name, "utc_offset_hours": rt.utc_offset_hours, "dst_in_effect": rt.dst_in_effect,
                 "dst_period": describe_transitions(rt.year_transitions),
                 "utc_datetime": rt.utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "jd_ut": round(sky["jd_ut"], 6), "jd_tt": round(sky["jd_tt"], 6),
                 "delta_t_seconds": round(sky["delta_t_seconds"], 3), "zone_abbrev": rt.abbrev,
                 "sources": [f"IANA tzdata (zoneinfo), zone {rt.tz_name}"]},
        "settings": {"zodiac": "tropical", "house_system": (sky["house_system"] or "none").capitalize(),
                     "positions": "geocentric apparent, true ecliptic and equinox of date",
                     "engines": [f"{sky['engine']} (canonical values)", f"{swe['engine']} (confirmation)"]},
        "planets": planets,
        "aspects": aspects,
        "elements": elements,
        "modalities": modalities,
        "counting_policy": "Elements/modalities: 10 planets (Sun–Pluto), unweighted; angles reported separately.",
        "aspect_policy": {"bodies": "10 planets + ASC + MC" if time_known else "10 planets",
                          "orbs": {"conjunction": 8, "sextile": 5, "square": 7, "trine": 8, "opposition": 8},
                          "luminary_bonus": "+2° when Sun or Moon involved",
                          "applying_rule": "orb decreasing under instantaneous planetary speeds; ASC/MC held fixed"},
        "moon_ingress": moon,
    }
    if sky["house_system"] == "porphyry":
        chart["settings"]["house_system_note"] = ("Placidus is undefined at this latitude; Porphyry houses "
                                                  "(equal trisection of each quadrant) are used instead.")
        if sky.get("asc_flipped"):
            chart["settings"]["house_system_note"] += (" Above the polar circle the Ascendant is taken as the "
                                                       "ecliptic point in the eastern quadrant (Swiss Ephemeris convention).")
    derived: dict = {"retrograde_planets": [p["name"] for p in planets if p["retrograde"]],
                     "essential_dignity_traditional_planets": _dignity(pl),
                     "dispositors": {p["name"]: {"traditional": TRAD_RULER[p["sign"]], "modern": MODERN_RULER[p["sign"]]}
                                     for p in planets}}
    bodies = PLANETS[:]
    if time_known:
        chart["time"].update({"gast_hours": round(sky["gast_hours"], 8), "ramc_deg": round(sky["ramc_deg"], 6),
                              "true_obliquity_deg": round(sky["true_obliquity_deg"], 6)})
        angles = {"ASC": fmt_point(sky["angles"]["ASC"]), "DSC": fmt_point((sky["angles"]["ASC"] + 180) % 360),
                  "MC": fmt_point(sky["angles"]["MC"]), "IC": fmt_point((sky["angles"]["MC"] + 180) % 360)}
        houses = []
        for i, c in enumerate(cusps):
            s = sign_of(c)
            houses.append({"house": i + 1, **fmt_point(c), "ruler_traditional": TRAD_RULER[s],
                           "ruler_modern": MODERN_RULER[s],
                           "occupants": [p["name"] for p in planets if p["house"] == i + 1]})
        cusp_signs = {sign_of(c) for c in cusps}
        intercepted = [s for s in SIGNS if s not in cusp_signs]
        asc_sign, mc_sign = angles["ASC"]["sign"], angles["MC"]["sign"]
        ruler = TRAD_RULER[asc_sign]
        chart.update({"angles": angles, "houses": houses, "intercepted_signs": intercepted,
                      "intercepted_note": _intercepted_note(intercepted, houses),
                      "angle_signs": {"ASC": {"sign": asc_sign, "element": ELEMENT[asc_sign], "modality": MODALITY[asc_sign]},
                                      "MC": {"sign": mc_sign, "element": ELEMENT[mc_sign], "modality": MODALITY[mc_sign]}}})
        derived = {
            "chart_ruler": {"planet": ruler, "basis": f"traditional ruler of the {asc_sign} Ascendant",
                            "position": pl[ruler]["display"], "house": pl[ruler]["house"]},
            "planets_per_house": {str(h["house"]): len(h["occupants"]) for h in houses},
            "hemispheres": {"below_horizon_houses_1_6": [p["name"] for p in planets if 1 <= p["house"] <= 6],
                            "above_horizon_houses_7_12": [p["name"] for p in planets if 7 <= p["house"] <= 12],
                            "eastern_houses_10_3": [p["name"] for p in planets if p["house"] in (10, 11, 12, 1, 2, 3)],
                            "western_houses_4_9": [p["name"] for p in planets if p["house"] in (4, 5, 6, 7, 8, 9)]},
            **{k: derived[k] for k in ("dispositors",)},
            "houses_ruled_by_planet": {n: {"traditional": [h["house"] for h in houses if h["ruler_traditional"] == n],
                                           "modern": [h["house"] for h in houses if h["ruler_modern"] == n]} for n in PLANETS},
            **{k: derived[k] for k in ("essential_dignity_traditional_planets", "retrograde_planets")},
        }
        bodies += ["ASC", "MC"]
        sens = SENS.scan(jd_swe, b.lat, b.lon, sky["house_system"], aspects)
        chart["sensitivity"] = sens
    chart["aspect_patterns"] = _patterns(aspects, planets, bodies)
    chart["derived"] = derived
    chart["sensitivity_caveats"] = SENS.caveats(chart.get("sensitivity", {}), planets, moon, time_known)
    return chart, gate
