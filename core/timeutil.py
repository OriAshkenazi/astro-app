"""Local civil time -> UTC using IANA tzdata (zoneinfo), with DST-edge detection."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo


class LocalTimeError(ValueError):
    """The local time is ambiguous (clocks went back) or does not exist (clocks went forward).

    `options` lists the valid interpretations; the UI asks the user to pick one and
    resubmits with `fold` set.
    """

    def __init__(self, kind: str, options: list[dict]):
        self.kind = kind
        self.options = options
        super().__init__(f"local time is {kind}: {options}")


@dataclass(frozen=True)
class ResolvedTime:
    local: dt.datetime
    utc: dt.datetime
    tz_name: str
    utc_offset_hours: float
    dst_in_effect: bool
    abbrev: str
    year_transitions: list[dict]


def _offset_h(d: dt.datetime) -> float:
    return d.utcoffset().total_seconds() / 3600.0


def year_transitions(tz_name: str, year: int) -> list[dict]:
    """Every UTC-offset change in `year`, located to the minute."""
    tz = ZoneInfo(tz_name)
    utc = dt.timezone.utc
    t = dt.datetime(year, 1, 1, tzinfo=utc)
    end = dt.datetime(year + 1, 1, 1, tzinfo=utc)
    out = []
    prev = t.astimezone(tz)
    step = dt.timedelta(hours=6)
    while t < end:
        nxt_t = min(t + step, end)
        nxt = nxt_t.astimezone(tz)
        if nxt.utcoffset() != prev.utcoffset():
            lo, hi = t, nxt_t
            while hi - lo > dt.timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                if mid.astimezone(tz).utcoffset() == prev.utcoffset():
                    lo = mid
                else:
                    hi = mid
            after = hi.astimezone(tz)
            out.append({"utc": hi.strftime("%Y-%m-%dT%H:%MZ"),
                        "local_before": (hi - dt.timedelta(minutes=1)).astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                        "local_after": after.strftime("%Y-%m-%d %H:%M"),
                        "new_utc_offset_hours": _offset_h(after),
                        "dst": bool(after.dst()), "abbrev": after.tzname()})
        prev, t = nxt, nxt_t
    return out


def resolve_local(date: dt.date, time: dt.time, tz_name: str, fold: int | None = None) -> ResolvedTime:
    tz = ZoneInfo(tz_name)
    naive = dt.datetime.combine(date, time)
    c0, c1 = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if c0.utcoffset() != c1.utcoffset() and fold is None:
        roundtrip = c0.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
        kind = "nonexistent" if roundtrip != naive else "ambiguous"
        opts = [{"fold": f, "utc_offset_hours": _offset_h(c), "abbrev": c.tzname(),
                 "utc": c.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} for f, c in ((0, c0), (1, c1))]
        raise LocalTimeError(kind, opts)
    local = c1 if fold == 1 else c0
    return ResolvedTime(local=local, utc=local.astimezone(dt.timezone.utc), tz_name=tz_name,
                        utc_offset_hours=_offset_h(local), dst_in_effect=bool(local.dst()),
                        abbrev=local.tzname() or "", year_transitions=year_transitions(tz_name, date.year))


def describe_transitions(trs: list[dict]) -> str:
    if not trs:
        return "no UTC-offset changes that year"
    parts = [f"{t['local_before']} → {t['local_after']} local (UTC{t['new_utc_offset_hours']:+g}, "
             f"{'DST' if t['dst'] else 'standard'}; {t['utc']})" for t in trs]
    return "; ".join(parts)
