"""Canonical-value engine: Skyfield + JPL DE421, own angle / Placidus math.

Ported from the reference project's 02_audit/audit.py. Rounding order is preserved
(planet longitudes are rounded to 6 dp *before* houses and aspects are derived) because
the canonical dataset is built from exactly these numbers.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

from skyfield.api import Loader
from skyfield.framelib import ecliptic_frame

from .. import paths
from .constants import ASPECT_ANGLES, PLANETS, house_of, norm, orb_limit, separation

BODIES = dict(zip(PLANETS, ["sun", "moon", "mercury", "venus", "mars barycenter", "jupiter barycenter",
                            "saturn barycenter", "uranus barycenter", "neptune barycenter", "pluto barycenter"]))
ENGINE_NAME = "Skyfield with the JPL DE421 ephemeris (angles and houses by own spherical-astronomy code)"


class PlacidusUndefined(ValueError):
    pass


def ephe_dir() -> Path:
    return paths.ephe_dir()


@lru_cache(maxsize=1)
def _kernel():
    loader = Loader(str(ephe_dir()))
    return loader.timescale(), loader("de421.bsp")


def _ecl(eph, body, t):
    ap = eph["earth"].at(t).observe(eph[body]).apparent()
    lat, lon, _ = ap.frame_latlon(ecliptic_frame)  # true ecliptic & equinox of date
    return float(lon.degrees), float(lat.degrees)


def planets_at(utc_dt) -> tuple[list[dict], object]:
    ts, eph = _kernel()
    t = ts.from_datetime(utc_dt)
    out = []
    for name in PLANETS:
        lon, blat = _ecl(eph, BODIES[name], t)
        lon_m, _ = _ecl(eph, BODIES[name], ts.tt_jd(t.tt - 0.5))
        lon_p, _ = _ecl(eph, BODIES[name], ts.tt_jd(t.tt + 0.5))
        speed = float(((lon_p - lon_m + 540) % 360) - 180)  # deg/day, central difference over 1 day
        out.append({"name": name, "lon": round(norm(lon), 6), "ecl_lat": round(blat, 4),
                    "speed": round(speed, 6), "retrograde": bool(speed < 0)})
    return out, t


def angles_and_cusps(t, lat: float, lon: float, system: str = "placidus") -> dict:
    gast_h = float(t.gast)
    ramc = norm(gast_h * 15.0 + lon)
    dpsi, deps = t._nutation_angles_radians
    eps = float(t._mean_obliquity_radians + deps)
    phi = math.radians(lat)
    R = math.radians(ramc)
    mc = norm(math.degrees(math.atan2(math.sin(R), math.cos(R) * math.cos(eps))))
    asc = norm(math.degrees(math.atan2(math.cos(R), -(math.sin(R) * math.cos(eps) + math.tan(phi) * math.sin(eps)))))

    def ra_to_lon(ra_deg):
        a = math.radians(ra_deg)
        return norm(math.degrees(math.atan2(math.sin(a), math.cos(a) * math.cos(eps))))

    def sda(lon_deg):
        delta = math.asin(math.sin(eps) * math.sin(math.radians(lon_deg)))
        x = -math.tan(phi) * math.tan(delta)
        if abs(x) > 1.0:
            raise PlacidusUndefined(f"circumpolar ecliptic point at latitude {lat}")
        return math.degrees(math.acos(x))

    def placidus_cusp(house):
        cur = norm(mc + 30 * (house - 10)) if house > 3 else norm(mc + 180 + 30 * (house - 4))
        for _ in range(200):
            D = sda(cur)
            SNA = 180.0 - D
            ra = {11: ramc + D / 3, 12: ramc + 2 * D / 3,
                  2: ramc + 180 - 2 * SNA / 3, 3: ramc + 180 - SNA / 3}[house]
            new = ra_to_lon(norm(ra))
            if abs(((new - cur + 540) % 360) - 180) < 1e-10:
                return new
            cur = new
        return cur

    asc_flipped = False
    if not 0 < norm(asc - mc) < 180:
        if system == "placidus":
            raise PlacidusUndefined("ASC not in eastern half (polar latitude)")
        asc, asc_flipped = norm(asc + 180), True  # Swiss Ephemeris polar convention
    cusps = {1: asc, 10: mc}
    if system == "placidus":
        for h in (11, 12, 2, 3):
            cusps[h] = placidus_cusp(h)
    else:  # porphyry: trisect each quadrant in ecliptic longitude
        q1 = norm(asc - mc)
        q2 = norm(mc + 180 - asc)
        cusps[11], cusps[12] = norm(mc + q1 / 3), norm(mc + 2 * q1 / 3)
        cusps[2], cusps[3] = norm(asc + q2 / 3), norm(asc + 2 * q2 / 3)
    for h in (1, 2, 3, 10, 11, 12):
        cusps[(h + 6 - 1) % 12 + 1] = norm(cusps[h] + 180)
    return {"asc": asc, "mc": mc, "cusps": [cusps[h] for h in range(1, 13)], "gast_hours": gast_h,
            "ramc_deg": ramc, "true_obliquity_deg": math.degrees(eps), "house_system": system,
            "asc_flipped": asc_flipped}


def aspects_for(points: list[tuple[str, float, float | None]], angle_names=("ASC", "MC")) -> list[dict]:
    """points: (name, lon, speed or None for angles). Angles are held fixed (natal convention)."""
    H = 1e-4
    res = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            n1, l1, s1 = points[i]
            n2, l2, s2 = points[j]
            if n1 in angle_names and n2 in angle_names:
                continue
            d = separation(l1, l2)
            for typ, ang in ASPECT_ANGLES.items():
                lim = orb_limit(n1, n2, typ)
                o = abs(d - ang)
                if o <= lim:
                    d2 = separation(l1 + (s1 or 0.0) * H, l2 + (s2 or 0.0) * H)
                    res.append({"p1": n1, "p2": n2, "type": typ, "angle": ang, "orb_exact": round(o, 6),
                                "applying": bool(abs(d2 - ang) < o), "orb_limit": lim,
                                "margin_to_limit": round(lim - o, 4)})
    res.sort(key=lambda a: a["orb_exact"])
    return res


def compute(utc_dt, lat: float, lon: float, time_known: bool = True) -> dict:
    planets, t = planets_at(utc_dt)
    out = {"engine": ENGINE_NAME, "planets": planets, "jd_ut": float(t.ut1), "jd_tt": float(t.tt),
           "delta_t_seconds": float(t.delta_t), "angles": None, "cusps": None, "house_system": None}
    pts = [(p["name"], p["lon"], p["speed"]) for p in planets]
    if time_known:
        try:
            ac = angles_and_cusps(t, lat, lon, "placidus")
        except PlacidusUndefined:
            ac = angles_and_cusps(t, lat, lon, "porphyry")
        out.update({"angles": {"ASC": ac["asc"], "MC": ac["mc"]}, "cusps": ac["cusps"],
                    "house_system": ac["house_system"], "asc_flipped": ac["asc_flipped"],
                    **{k: ac[k] for k in ("gast_hours", "ramc_deg", "true_obliquity_deg")}})
        for p in planets:
            p["house"] = house_of(p["lon"], ac["cusps"])
        pts += [("ASC", ac["asc"], None), ("MC", ac["mc"], None)]
    out["aspects"] = aspects_for(pts)
    return out
