"""Confirmation engine: Swiss Ephemeris (pyswisseph). Ported from 01_calc/calc.py.

Uses Swiss Ephemeris data files if present in ASTRO_EPHE_DIR (sepl_18.se1 / semo_18.se1),
otherwise the built-in Moshier ephemeris. Also used for time-sensitivity scans because
it is ~100x faster than the Skyfield engine.
"""
from __future__ import annotations

import os

import swisseph as swe

from .constants import PLANETS, house_of, norm
from .engine_skyfield import aspects_for, ephe_dir

_IDS = dict(zip(PLANETS, [swe.SUN, swe.MOON, swe.MERCURY, swe.VENUS, swe.MARS, swe.JUPITER,
                          swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO]))
_HSYS = {"placidus": b"P", "porphyry": b"O"}
_FLAG = None


def _flag() -> int:
    global _FLAG
    if _FLAG is None:
        d = ephe_dir()
        if (d / "sepl_18.se1").is_file() and (d / "semo_18.se1").is_file():
            swe.set_ephe_path(str(d))
            _FLAG = swe.FLG_SWIEPH
        else:
            swe.set_ephe_path(None)
            _FLAG = swe.FLG_MOSEPH
    return _FLAG | swe.FLG_SPEED


def engine_name() -> str:
    return f"pyswisseph {swe.version} ({'Swiss Ephemeris files' if _flag() & swe.FLG_SWIEPH else 'Moshier'})"


def jd_from_utc(utc_dt) -> float:
    h = utc_dt.hour + utc_dt.minute / 60 + utc_dt.second / 3600 + utc_dt.microsecond / 3.6e9
    return swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, h, swe.GREG_CAL)


def planet_lons(jd: float) -> dict[str, tuple[float, float]]:
    f = _flag()
    return {n: (xx[0], xx[3]) for n, pid in _IDS.items() for xx in [swe.calc_ut(jd, pid, f)[0]]}


def houses(jd: float, lat: float, lon: float, system: str) -> tuple[list[float], float, float]:
    cusps, ascmc = swe.houses_ex(jd, lat, lon, _HSYS[system])
    return list(cusps[:12]), ascmc[0], ascmc[1]


def compute(utc_dt, lat: float, lon: float, time_known: bool = True, house_system: str = "placidus") -> dict:
    jd = jd_from_utc(utc_dt)
    pl = planet_lons(jd)
    planets = [{"name": n, "lon": norm(l), "speed": s, "retrograde": s < 0} for n, (l, s) in pl.items()]
    out = {"engine": engine_name(), "planets": planets, "jd_ut": jd, "angles": None, "cusps": None}
    pts = [(p["name"], p["lon"], p["speed"]) for p in planets]
    if time_known:
        cusps, asc, mc = houses(jd, lat, lon, house_system)
        out.update({"angles": {"ASC": asc, "MC": mc}, "cusps": cusps})
        for p in planets:
            p["house"] = house_of(p["lon"], cusps)
        pts += [("ASC", asc, None), ("MC", mc, None)]
    out["aspects"] = aspects_for(pts)
    return out
