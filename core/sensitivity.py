"""Birth-time sensitivity: how far the recorded time can move before a chart fact changes.

Replaces the hand-written `sensitivity_caveats` of the reference project with a scan:
ASC/MC sign changes, planet house changes, cusp sign changes, angle aspects dropping
out of orb, and Moon sign ingress. Swiss Ephemeris is used for speed.
"""
from __future__ import annotations

from . import engine_swe as E
from .constants import NEAR_ORB_LIMIT_DEG, house_of, orb_limit, separation, sign_of

MIN = 1.0 / 1440.0
HOUSE_WINDOW_MIN = 120
ANGLE_WINDOW_MIN = 1440
REPORT_WITHIN_MIN = 10.0


def _find_change(state, base, direction: int, max_min: int) -> float | None:
    """Minutes (to ~0.05 s) until state(minutes) != base, scanning in 1-minute steps."""
    prev = 0.0
    for k in range(1, max_min + 1):
        m = direction * k
        if state(m) != base:
            lo, hi = prev, float(m)
            for _ in range(25):
                mid = (lo + hi) / 2
                if state(mid) == base:
                    lo = mid
                else:
                    hi = mid
            return abs(hi)
        prev = float(m)
    return None


def scan(jd: float, lat: float, lon: float, house_system: str, aspects: list[dict]) -> dict:
    def snap(minutes):
        t = jd + minutes * MIN
        cusps, asc, mc = E.houses(t, lat, lon, house_system)
        return cusps, asc, mc, E.planet_lons(t)

    out = {"asc_sign_change_minutes": {}, "mc_sign_change_minutes": {}, "planet_house_change_minutes": {},
           "cusp_sign_change_minutes": {}, "angle_aspect_dropout_minutes": [], "weak_aspects": []}

    for key, idx in (("asc_sign_change_minutes", 1), ("mc_sign_change_minutes", 2)):
        f = lambda m, i=idx: sign_of(E.houses(jd + m * MIN, lat, lon, house_system)[i])
        base = f(0)
        for d, lbl in ((-1, "earlier"), (1, "later")):
            mins = _find_change(f, base, d, ANGLE_WINDOW_MIN)
            out[key][lbl] = None if mins is None else round(mins, 2)
            out[key][f"{lbl}_sign"] = None if mins is None else f(d * (mins + 0.01))

    cusps0, _, _, pl0 = snap(0)
    for name, (plon, _) in pl0.items():
        f = lambda m, n=name: (lambda s: house_of(s[3][n][0], s[0]))(snap(m))
        base = house_of(plon, cusps0)
        rec = {"house": base}
        for d, lbl in ((-1, "earlier"), (1, "later")):
            mins = _find_change(f, base, d, HOUSE_WINDOW_MIN)
            rec[lbl] = None if mins is None else round(mins, 2)
            rec[f"{lbl}_house"] = None if mins is None else f(d * (mins + 0.01))
        out["planet_house_change_minutes"][name] = rec

    for h in range(12):
        f = lambda m, i=h: sign_of(E.houses(jd + m * MIN, lat, lon, house_system)[0][i])
        base = sign_of(cusps0[h])
        rec = {"sign": base}
        for d, lbl in ((-1, "earlier"), (1, "later")):
            mins = _find_change(f, base, d, HOUSE_WINDOW_MIN)
            rec[lbl] = None if mins is None else round(mins, 2)
            rec[f"{lbl}_sign"] = None if mins is None else f(d * (mins + 0.01))
        out["cusp_sign_change_minutes"][str(h + 1)] = rec

    for a in aspects:
        if a["margin_to_limit"] >= NEAR_ORB_LIMIT_DEG:
            continue
        if {"ASC", "MC"} & {a["p1"], a["p2"]}:
            def present(m, a=a):
                cusps, asc, mc, pl = snap(m)
                pos = {"ASC": asc, "MC": mc, **{n: v[0] for n, v in pl.items()}}
                o = abs(separation(pos[a["p1"]], pos[a["p2"]]) - a["angle"])
                return o <= orb_limit(a["p1"], a["p2"], a["type"])
            rec = {k: a[k] for k in ("p1", "p2", "type", "margin_to_limit")}
            for d, lbl in ((-1, "earlier"), (1, "later")):
                mins = _find_change(present, True, d, HOUSE_WINDOW_MIN)
                rec[lbl] = None if mins is None else round(mins, 2)
            out["angle_aspect_dropout_minutes"].append(rec)
        else:
            out["weak_aspects"].append({k: a[k] for k in ("p1", "p2", "type", "margin_to_limit")})
    return out


def moon_ingress(jd: float) -> dict:
    lon, speed = E.planet_lons(jd)["Moon"]
    d = lon % 30
    return {"hours_since_ingress": round(d / speed * 24, 2), "hours_to_next_sign": round((30 - d) / speed * 24, 2),
            "next_sign": sign_of(lon + 30 - d + 1e-6)}


def _t(m: float) -> str:
    if m < 1:
        return f"~{round(m * 60)} s"
    if m < 90:
        return f"~{m:.1f} min".replace(".0 min", " min")
    return f"~{m / 60:.1f} h"


def caveats(sens: dict, planets: list[dict], moon: dict, time_known: bool) -> list[str]:
    """Plain-English caveats from the scan (deterministic; the LLM never writes these)."""
    if not time_known:
        out = ["Birth time unknown: positions are computed for 12:00 local time; houses, the Ascendant, "
               "the Midheaven and aspects to them are omitted."]
        if min(moon["hours_since_ingress"], moon["hours_to_next_sign"]) < 12:
            out.append(f"The Moon changes sign on the birth day (into {moon['next_sign']} "
                       f"{moon['hours_to_next_sign']:.1f} h after noon or entered its sign "
                       f"{moon['hours_since_ingress']:.1f} h before); its sign is uncertain.")
        return out
    pl = {p["name"]: p for p in planets}
    out = []
    for name, r in sens["planet_house_change_minutes"].items():
        for lbl in ("earlier", "later"):
            if r[lbl] is not None and r[lbl] <= REPORT_WITHIN_MIN:
                out.append(f"{name} ({pl[name]['display']}) is in house {r['house']} at the recorded time; "
                           f"a birth {_t(r[lbl])} {lbl} would place it in house {r[f'{lbl}_house']}.")
    seen = set()
    for h, r in sens["cusp_sign_change_minutes"].items():
        h = int(h)
        opp = (h + 5) % 12 + 1
        if h in seen:
            continue
        for lbl in ("earlier", "later"):
            if r[lbl] is not None and r[lbl] <= REPORT_WITHIN_MIN:
                o = sens["cusp_sign_change_minutes"][str(opp)]
                seen.update({h, opp})
                a, b = sorted((h, opp))
                sa = sens["cusp_sign_change_minutes"][str(a)]
                sb = sens["cusp_sign_change_minutes"][str(b)]
                out.append(f"Cusps {a} and {b} are at {sa['sign']} / {sb['sign']}; a birth {_t(r[lbl])} {lbl} "
                           f"would put them in {sa[f'{lbl}_sign']} / {sb[f'{lbl}_sign']}.")
                break
    for a in sens["angle_aspect_dropout_minutes"]:
        when = [f"{_t(a[l])} {l}" for l in ("earlier", "later") if a[l] is not None and a[l] <= 30]
        out.append(f"{a['p1']}–{a['p2']} {a['type']} is only {a['margin_to_limit']:.2f}° inside its orb limit"
                   + (f" and drops out with a birth {' or '.join(when)}" if when else "") + "; treat it as weak.")
    if sens["weak_aspects"]:
        names = ", ".join(f"{w['p1']}–{w['p2']} {w['type']} ({w['margin_to_limit']:.2f}°)" for w in sens["weak_aspects"])
        out.append(f"Wide aspects near their orb limit (margin in brackets): {names}. Treat these as weak.")
    def shift(r):
        parts = [f"{_t(r[l])} {l} (to {r[f'{l}_sign']})" for l in ("earlier", "later") if r[l] is not None]
        return " or ".join(parts) if parts else "not within 24 h"
    out.append(f"The Ascendant changes sign with a birth {shift(sens['asc_sign_change_minutes'])}; "
               f"the Midheaven with a birth {shift(sens['mc_sign_change_minutes'])}.")
    out.append("House-level conclusions assume the recorded birth time is accurate to about ±1 minute; "
               "factors listed above are time-sensitive.")
    return out
