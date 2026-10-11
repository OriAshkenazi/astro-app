"""Shared astrological constants and string formatting.

Formatting rules follow 00_plan/CHART_SCHEMA.md of the reference project and are
byte-compatible with 03_canonical/build_canonical.py (the golden fixture depends on it).
"""
from __future__ import annotations

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
         "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
ELEMENT = dict(zip(SIGNS, ["Fire", "Earth", "Air", "Water"] * 3))
MODALITY = dict(zip(SIGNS, ["Cardinal", "Fixed", "Mutable"] * 4))
TRAD_RULER = dict(zip(SIGNS, ["Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury", "Venus",
                              "Mars", "Jupiter", "Saturn", "Saturn", "Jupiter"]))
MODERN_RULER = dict(TRAD_RULER, Scorpio="Pluto", Aquarius="Uranus", Pisces="Neptune")
PLANETS = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
LUMINARIES = {"Sun", "Moon"}

ASPECT_ANGLES = {"conjunction": 0, "sextile": 60, "square": 90, "trine": 120, "opposition": 180}
ORBS = {"conjunction": 8, "sextile": 5, "square": 7, "trine": 8, "opposition": 8}
LUMINARY_BONUS = 2.0
NEAR_CUSP_DEG = 2.0
NEAR_ORB_LIMIT_DEG = 0.25

DOMICILE = {"Sun": ["Leo"], "Moon": ["Cancer"], "Mercury": ["Gemini", "Virgo"], "Venus": ["Taurus", "Libra"],
            "Mars": ["Aries", "Scorpio"], "Jupiter": ["Sagittarius", "Pisces"], "Saturn": ["Capricorn", "Aquarius"]}
EXALTATION = {"Sun": "Aries", "Moon": "Taurus", "Mercury": "Virgo", "Venus": "Pisces", "Mars": "Capricorn",
              "Jupiter": "Cancer", "Saturn": "Libra"}

# Tolerances for the two-engine gate (G1)
TOL_PLANET_DEG = 0.02
TOL_ANGLE_DEG = 0.05


def norm(x: float) -> float:
    return x % 360.0


def sign_of(lon: float) -> str:
    return SIGNS[int(lon // 30) % 12]


def separation(a: float, b: float) -> float:
    d = abs(norm(a - b))
    return 360.0 - d if d > 180.0 else d


def signed_diff(a: float, b: float) -> float:
    return (a - b + 540.0) % 360.0 - 180.0


def dms(deg_in_sign: float) -> str:
    """Degrees+minutes in sign, minutes truncated (e.g. 3°58′)."""
    d = int(deg_in_sign)
    m = int(round((deg_in_sign - d) * 60 * 1e6) / 1e6)  # guard float error, then truncate
    return f"{d}°{m:02d}′"


def orb_dms(orb: float) -> str:
    tot = int(round(abs(orb) * 60))
    return f"{tot // 60}°{tot % 60:02d}′"


def coord_dms(v: float, pos: str, neg: str) -> str:
    h = pos if v >= 0 else neg
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = round(((v - d) * 60 - m) * 60)
    if s == 60:
        s = 0
        m += 1
    if m == 60:
        m = 0
        d += 1
    return f"{d}°{m:02d}′{s:02d}″{h}"


def fmt_point(lon: float) -> dict:
    s = sign_of(lon)
    return {"lon": round(lon, 6), "sign": s, "deg_in_sign": round(lon % 30, 6), "dms": dms(lon % 30),
            "display": f"{s} {dms(lon % 30)}"}


def house_of(lon: float, cusps: list[float]) -> int:
    """House N if cusp_N <= lon < cusp_N+1 (circular)."""
    for i in range(12):
        a, b = cusps[i], cusps[(i + 1) % 12]
        if norm(lon - a) < norm(b - a):
            return i + 1
    raise ValueError(f"no house for {lon}")


def orb_limit(p1: str, p2: str, typ: str) -> float:
    return ORBS[typ] + (LUMINARY_BONUS if {p1, p2} & LUMINARIES else 0.0)
