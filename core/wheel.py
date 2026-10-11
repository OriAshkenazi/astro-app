"""Interactive natal-chart wheel as plain SVG (no matplotlib).

Ported from the reference project's 05_design/wheel.py: same geometry, conventions and
collision-free glyph layout. Differences for the web:
  * every chart factor is a <g> with `data-factor` ("planet:Sun", "house:3", "sign:Virgo",
    "angle:ASC", "aspect:Sun|Pluto|sextile"), focusable and labelled for screen readers;
  * colours are CSS variables (`--wheel-*`) with the reference palette as fallback, so the
    host page can theme light/dark;
  * an unknown birth time draws planets only, with 0° Aries on the left.

Conventions: ASC at 9 o'clock, zodiac runs counter-clockwise,
screen angle = 180 + (lon - ASC), SVG y axis points down.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from html import escape

from .constants import ELEMENT, SIGNS

S = 100.0  # SVG units per unit radius
PT = S / 330.0  # one point of the reference 11-inch figure (1 unit radius = 330 pt)

SIGN_GLYPH = {s: chr(0x2648 + i) + "︎" for i, s in enumerate(SIGNS)}  # FE0E: text, not emoji
PLANET_GLYPH = {n: g + "︎" for n, g in {"Sun": "☉", "Moon": "☽", "Mercury": "☿", "Venus": "♀", "Mars": "♂",
                                            "Jupiter": "♃", "Saturn": "♄", "Uranus": "♅", "Neptune": "♆",
                                            "Pluto": "♇"}.items()}
RETRO = "℞"
ASPECT_STYLE = {"trine": ("blue", 1.5, None), "sextile": ("blue-soft", 1.4, (5, 3)),
                "square": ("terra", 1.5, None), "opposition": ("terra", 2.0, None)}
PALETTE = {"paper": "#F7F3EA", "ink": "#22262F", "ink-2": "#4B5161", "midnight": "#1D2B45", "gold": "#B3925A",
           "gold-text": "#7A5A28", "blue": "#4C6A88", "blue-soft": "#8FA8BF", "terra": "#9A4B31",
           "tint-fire": "#F1DDD3", "tint-earth": "#ECE1C8", "tint-air": "#E3E8EC", "tint-water": "#DCE4EC"}

R_OUT, R_ZI, R_TICK_IN = 1.000, 0.860, 0.825
R_PG, R_PD, R_PR = 0.772, 0.688, 0.622
R_HO, R_HI, R_HN = 0.590, 0.505, 0.548
R_CUSP_LBL, R_AXIS_LBL = 1.048, 1.125
MIN_SEP, MIN_SEP_HARD = 7.2, 5.6
CUSP_CLR, AXIS_CLR = 4.2, 4.5
AXIS_HOUSES = (1, 4, 7, 10)
AXIS_NAME = {1: "ASC", 4: "IC", 7: "DSC", 10: "MC"}


@dataclass
class WheelModel:
    ref_lon: float                       # longitude drawn at 9 o'clock
    planets: list[dict]                  # name, lon, display_lon, dms, retrograde, house
    cusps: list[float] | None
    angles: dict[str, float]
    aspects: list[dict]
    warnings: list[str] = field(default_factory=list)

    def theta(self, lon: float) -> float:
        return (180.0 + (lon - self.ref_lon)) % 360.0

    def xy(self, lon: float, r: float) -> tuple[float, float]:
        t = math.radians(self.theta(lon))
        return round(r * math.cos(t) * S, 3), round(-r * math.sin(t) * S, 3)


# ----------------------------------------------------------------- layout
def spread(lons: list[float], sep: float) -> list[float]:
    """Displace glyph angles so neighbours are >= sep apart, centred on cluster means."""
    n = len(lons)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: lons[i] % 360)
    srt = [lons[i] % 360 for i in order]
    gaps = [((srt[(k + 1) % n] - srt[k]) % 360) or 360 for k in range(n)]
    start = (gaps.index(max(gaps)) + 1) % n
    order = order[start:] + order[:start]
    base = lons[order[0]] % 360
    unwrapped = [base + ((lons[i] - base) % 360) for i in order]
    pos = list(unwrapped)
    clusters = [[k, k] for k in range(n)]

    def place(c):
        a, b = c
        m = sum(unwrapped[a:b + 1]) / (b - a + 1)
        for j, k in enumerate(range(a, b + 1)):
            pos[k] = m - sep * (b - a) / 2 + j * sep

    changed = True
    while changed:
        changed = False
        for ci in range(len(clusters) - 1):
            a, b = clusters[ci], clusters[ci + 1]
            if pos[b[0]] - pos[a[1]] < sep - 1e-9:
                clusters[ci:ci + 2] = [[a[0], b[1]]]
                place(clusters[ci])
                changed = True
                break
    out = [0.0] * n
    for k, i in enumerate(order):
        out[i] = pos[k] % 360
    return out


def layout_in_houses(lons: list[float], cusps: list[float], warnings: list[str]) -> list[float]:
    """Each glyph stays inside its own house, clear of cusp lines, >= MIN_SEP apart
    (packed to MIN_SEP_HARD when crowded; spills over a non-axis cusp only as last resort)."""
    cl = [c % 360 for c in cusps]
    out = [0.0] * len(lons)
    groups: dict[int, list[int]] = {}
    for i, lon in enumerate(lons):
        for j in range(12):
            if (lon - cl[j]) % 360 < (cl[(j + 1) % 12] - cl[j]) % 360:
                groups.setdefault(j, []).append(i)
                break
    for j, idx in groups.items():
        hn, hn_next = j + 1, (j + 1) % 12 + 1
        width = (cl[(j + 1) % 12] - cl[j]) % 360
        lo = AXIS_CLR if hn in AXIS_HOUSES else CUSP_CLR
        hi = width - (AXIS_CLR if hn_next in AXIS_HOUSES else CUSP_CLR)
        idx.sort(key=lambda i: (lons[i] - cl[j]) % 360)
        x = [(lons[i] - cl[j]) % 360 for i in idx]
        n = len(x)
        sep = MIN_SEP
        if n > 1 and (n - 1) * sep > hi - lo:
            sep = max((hi - lo) / (n - 1), MIN_SEP_HARD)
        if (n - 1) * sep > hi - lo:
            need = (n - 1) * sep - (hi - lo)
            can_lo, can_hi = hn not in AXIS_HOUSES, hn_next not in AXIS_HOUSES
            if can_lo and can_hi:
                lo, hi = lo - need / 2, hi + need / 2
            elif can_lo:
                lo -= need
            elif can_hi:
                hi += need
            warnings.append(f"house {hn}: {n} glyphs do not fit; spilled over a cusp")
        pos = list(x)
        clusters = [[k, k] for k in range(n)]

        def place(c):
            a, b = c
            m = b - a
            first = sum(x[a:b + 1]) / (m + 1) - sep * m / 2
            first = min(max(first, lo), max(lo, hi - sep * m))
            for q, kk in enumerate(range(a, b + 1)):
                pos[kk] = first + q * sep

        for c in clusters:
            place(c)
        changed = True
        while changed:
            changed = False
            for ci in range(len(clusters) - 1):
                a, b = clusters[ci], clusters[ci + 1]
                if pos[b[0]] - pos[a[1]] < sep - 1e-9:
                    clusters[ci:ci + 2] = [[a[0], b[1]]]
                    place(clusters[ci])
                    changed = True
                    break
        for kk, i in enumerate(idx):
            out[i] = (cl[j] + pos[kk]) % 360
    return out


def build_model(chart: dict) -> WheelModel:
    angles = {k: v["lon"] for k, v in chart.get("angles", {}).items()}
    cusps = [h["lon"] for h in sorted(chart["houses"], key=lambda h: h["house"])] if "houses" in chart else None
    warnings: list[str] = []
    lons = [p["lon"] for p in chart["planets"]]
    disp = layout_in_houses(lons, cusps, warnings) if cusps else spread(lons, MIN_SEP)
    planets = [{"name": p["name"], "lon": p["lon"], "display_lon": d, "dms": p["dms"], "sign": p["sign"],
                "retrograde": p["retrograde"], "house": p.get("house")} for p, d in zip(chart["planets"], disp)]
    return WheelModel(ref_lon=angles.get("ASC", 0.0), planets=planets, cusps=cusps, angles=angles,
                      aspects=chart.get("aspects", []), warnings=warnings)


# ----------------------------------------------------------------- verification (port of --check)
def verify(m: WheelModel) -> list[str]:
    """Geometry read-back: returns a list of problems (empty = PASS)."""
    bad = []
    if m.angles.get("ASC") is not None and abs(m.theta(m.angles["ASC"]) - 180) > 1e-9:
        bad.append("ASC not at 9 o'clock")
    if (m.theta(m.ref_lon + 10) - m.theta(m.ref_lon)) % 360 != 10:
        bad.append("zodiac not counter-clockwise")
    from .constants import house_of
    for p in m.planets:
        if m.cusps:
            if house_of(p["display_lon"], m.cusps) != p["house"] and not m.warnings:
                bad.append(f"{p['name']} glyph drawn outside house {p['house']}")
            if not m.warnings:
                for i, c in enumerate(m.cusps):
                    clr = (AXIS_CLR if i + 1 in AXIS_HOUSES else CUSP_CLR) - 1e-6
                    if abs((p["display_lon"] - c + 180) % 360 - 180) < clr:
                        bad.append(f"{p['name']} glyph within {clr:.1f}° of cusp {i + 1}")
    ds = sorted(p["display_lon"] for p in m.planets)
    for a, b in zip(ds, ds[1:] + [ds[0] + 360]):
        if b - a < MIN_SEP_HARD - 1e-6:
            bad.append(f"glyphs {b - a:.2f}° apart (< {MIN_SEP_HARD}°)")
    return bad


# ----------------------------------------------------------------- SVG
def _rot_readable(t: float) -> float:
    t = (t + 180) % 360 - 180
    return t - 180 if t > 90 else t + 180 if t < -90 else t


def _var(tok: str) -> str:
    return f"var(--wheel-{tok}, {PALETTE[tok]})"


def _wedge(m: WheelModel, a: float, b: float, r1: float, r2: float) -> str:
    x1, y1 = m.xy(a, r2)
    x2, y2 = m.xy(b, r2)
    x3, y3 = m.xy(b, r1)
    x4, y4 = m.xy(a, r1)
    R2, R1 = r2 * S, r1 * S
    big = 1 if (b - a) % 360 > 180 else 0
    return f"M{x1} {y1}A{R2} {R2} 0 {big} 0 {x2} {y2}L{x3} {y3}A{R1} {R1} 0 {big} 1 {x4} {y4}Z"


def _text(x, y, s, cls, rot: float | None = None, extra: str = "") -> str:
    tr = f' transform="rotate({round(-rot, 2)} {x} {y})"' if rot else ""
    return f'<text x="{x}" y="{y}" class="{cls}"{tr}{extra}>{escape(s)}</text>'


def render_svg(chart: dict, title: str = "Natal chart wheel") -> str:
    m = build_model(chart)
    o: list[str] = []
    a = o.append
    a('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-121 -121 242 242" role="img" '
      f'aria-label="{escape(title)}" class="natal-wheel">')
    a("<style>"
      f".natal-wheel{{background:{_var('paper')}}}"
      f".w-txt{{font-family:var(--wheel-font,'Heebo','DejaVu Sans',sans-serif);fill:{_var('ink')};"
      "text-anchor:middle;dominant-baseline:central}"
      f".w-gly{{font-family:var(--wheel-glyph-font,'DejaVu Sans','Segoe UI Symbol','Noto Sans Symbols 2',sans-serif);"
      "text-anchor:middle;dominant-baseline:central}"
      f".w-sign{{font-size:{25 * PT:.2f}px;fill:{_var('midnight')}}}.w-pl{{font-size:{24 * PT:.2f}px;fill:{_var('midnight')}}}"
      f".w-dms{{font-size:{10 * PT:.2f}px}}.w-cl{{font-size:{9.5 * PT:.2f}px;fill:{_var('ink-2')}}}"
      f".w-hn{{font-size:{13 * PT:.2f}px;font-weight:500;fill:{_var('ink-2')}}}"
      f".w-ax{{font-size:{15 * PT:.2f}px;font-weight:700;fill:{_var('midnight')}}}"
      f".w-axd{{font-size:{10 * PT:.2f}px;font-weight:600;fill:{_var('midnight')}}}"
      f".w-rx{{font-size:{12 * PT:.2f}px;fill:{_var('terra')}}}"
      "[data-factor]{cursor:pointer;outline:none}"
      "[data-factor]:focus-visible,[data-factor].is-active{filter:drop-shadow(0 0 1.5px currentColor)}"
      ".w-asp.is-dim,[data-factor].is-dim{opacity:.18}"
      "</style>")
    a(f'<circle r="{R_OUT * S + 30}" fill="{_var("paper")}"/>')

    a('<g class="w-zodiac">')
    for i, s in enumerate(SIGNS):
        lab = f"{s}"
        a(f'<g data-factor="sign:{s}" tabindex="0" role="button" aria-label="{lab}">')
        a(f'<path d="{_wedge(m, i * 30, i * 30 + 30, R_ZI, R_OUT)}" fill="{_var("tint-" + ELEMENT[s].lower())}"/>')
        gx, gy = m.xy(i * 30 + 15, (R_OUT + R_ZI) / 2 + 0.012)
        a(_text(gx, gy, SIGN_GLYPH[s], "w-gly w-sign"))
        a("</g>")
    bounds = "".join(f"M{m.xy(i * 30, R_ZI)[0]} {m.xy(i * 30, R_ZI)[1]}L{m.xy(i * 30, R_OUT)[0]} {m.xy(i * 30, R_OUT)[1]}"
                     for i in range(12))
    a(f'<path d="{bounds}" stroke="{_var("gold")}" stroke-width="{1.1 * PT:.3f}" fill="none"/>')
    ticks = []
    for d in range(360):
        ln = 0.034 if d % 10 == 0 else (0.022 if d % 5 == 0 else 0.012)
        (x1, y1), (x2, y2) = m.xy(d, R_ZI), m.xy(d, R_ZI + ln)
        ticks.append(f"M{x1} {y1}L{x2} {y2}")
    a(f'<path d="{"".join(ticks)}" stroke="{_var("gold-text")}" stroke-width="{0.8 * PT:.3f}" fill="none"/>')
    rings = [(R_OUT, "gold", 1.6), (R_ZI, "gold", 1.2)] + ([(R_HO, "gold", 0.9)] if m.cusps else []) + [(R_HI, "ink-2", 1.2)]
    for r, tok, w in rings:
        a(f'<circle r="{r * S}" fill="none" stroke="{_var(tok)}" stroke-width="{w * PT:.3f}"/>')
    a("</g>")

    if m.cusps:
        a('<g class="w-houses">')
        for i, lon in enumerate(m.cusps):
            hn, heavy = i + 1, i + 1 in AXIS_HOUSES
            nxt = m.cusps[(i + 1) % 12]
            mid = lon + ((nxt - lon) % 360) / 2
            (xa, ya), (xb, yb), (x0, y0) = m.xy(lon, R_HO), m.xy(lon, R_ZI), m.xy(lon, R_HI)
            col = _var("midnight") if heavy else _var("ink-2")
            a(f'<g data-factor="house:{hn}" tabindex="0" role="button" aria-label="House {hn}, cusp '
              f'{escape(chart["houses"][i]["display"])}">')
            a(f'<path d="{_wedge(m, lon, lon + (nxt - lon) % 360, R_HI, R_HO)}" fill="transparent"/>')
            a(f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="{col}" stroke-width="{(1.8 if heavy else 0.6) * PT:.3f}" '
              f'opacity="{0.9 if heavy else 0.45}"/>')
            a(f'<line x1="{x0}" y1="{y0}" x2="{xa}" y2="{ya}" stroke="{col}" stroke-width="{(2.6 if heavy else 1.0) * PT:.3f}"/>')
            if heavy:
                xo, yo = m.xy(lon, R_OUT + 0.035)
                a(f'<line x1="{xb}" y1="{yb}" x2="{xo}" y2="{yo}" stroke="{col}" stroke-width="{2.6 * PT:.3f}"/>')
            else:
                lx, ly = m.xy(lon, R_CUSP_LBL)
                h = chart["houses"][i]
                a(_text(lx, ly, f"{h['dms']} {SIGN_GLYPH[h['sign']]}", "w-txt w-cl", _rot_readable(m.theta(lon) - 90)))
            nx, ny = m.xy(mid, R_HN)
            a(_text(nx, ny, str(hn), "w-txt w-hn"))
            a("</g>")
        a("</g>")
        a('<g class="w-angles">')
        for name in ("ASC", "DSC", "MC", "IC"):
            ang = chart["angles"][name]
            x, y = m.xy(ang["lon"], R_AXIS_LBL)
            a(f'<g data-factor="angle:{name}" tabindex="0" role="button" aria-label="{name} {escape(ang["display"])}">')
            a(_text(x, round(y - 8 * PT, 3), name, "w-txt w-ax"))
            a(_text(x, round(y + 6 * PT, 3), f"{ang['dms']} {SIGN_GLYPH[ang['sign']]}", "w-txt w-axd"))
            a("</g>")
        a("</g>")

    pos = {p["name"]: p["lon"] for p in m.planets} | {k: v for k, v in m.angles.items() if k in ("ASC", "MC")}
    a('<g class="w-aspects">')
    for asp in m.aspects:
        st = ASPECT_STYLE.get(asp["type"])
        if not st or asp["p1"] not in pos or asp["p2"] not in pos:
            continue
        tok, w, dash = st
        dash_attr = f' stroke-dasharray="{dash[0] * w * PT:.2f} {dash[1] * w * PT:.2f}"' if dash else ""
        (xa, ya), (xb, yb) = m.xy(pos[asp["p1"]], R_HI), m.xy(pos[asp["p2"]], R_HI)
        key = f"{asp['p1']}|{asp['p2']}|{asp['type']}"
        lab = f"{asp['p1']} {asp['type']} {asp['p2']}, orb {asp['orb_dms']}"
        a(f'<g class="w-asp w-asp-{asp["type"]}" data-factor="aspect:{key}" tabindex="0" role="button" '
          f'aria-label="{escape(lab)}">'
          f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="transparent" stroke-width="4"/>'
          f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="{_var(tok)}" stroke-width="{w * PT:.3f}" '
          f'stroke-linecap="round"{dash_attr}/></g>')
    for lon in pos.values():
        x, y = m.xy(lon, R_HI)
        a(f'<circle cx="{x}" cy="{y}" r="{1.3 * PT:.2f}" fill="{_var("ink-2")}"/>')
    a("</g>")

    a('<g class="w-planets">')
    for p in m.planets:
        lon, dl = p["lon"], p["display_lon"]
        (x1, y1), (x2, y2) = m.xy(lon, R_ZI), m.xy(lon, R_TICK_IN)
        gx0, gy0 = m.xy(dl, R_PG + 0.038)
        lab = f"{p['name']}, {p['sign']} {p['dms']}" + (f", house {p['house']}" if p["house"] else "") + \
              (", retrograde" if p["retrograde"] else "")
        a(f'<g data-factor="planet:{p["name"]}" tabindex="0" role="button" aria-label="{escape(lab)}">')
        a(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{_var("terra")}" stroke-width="{2.0 * PT:.3f}"/>')
        a(f'<circle cx="{x1}" cy="{y1}" r="{1.6 * PT:.2f}" fill="{_var("terra")}"/>')
        a(f'<line x1="{x2}" y1="{y2}" x2="{gx0}" y2="{gy0}" stroke="{_var("ink-2")}" stroke-width="{0.7 * PT:.3f}"/>')
        gx, gy = m.xy(dl, R_PG)
        a(f'<circle cx="{gx}" cy="{gy}" r="{12 * PT:.2f}" fill="transparent"/>')
        a(_text(gx, gy, PLANET_GLYPH[p["name"]], "w-gly w-pl"))
        dx, dy = m.xy(dl, R_PD)
        a(_text(dx, dy, p["dms"], "w-txt w-dms", _rot_readable(m.theta(dl))))
        if p["retrograde"]:
            rx, ry = m.xy(dl, R_PR)
            a(_text(rx, ry, RETRO, "w-gly w-rx"))
        a("</g>")
    a("</g>")
    a(f'<circle r="1.2" fill="{_var("gold")}"/>')
    a("</svg>")
    return "".join(o)
