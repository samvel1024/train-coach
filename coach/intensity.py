"""Intensity distribution: time in power/HR zones per week or month, 3-zone model.

  python3 -m coach.intensity [--by week|month] [--weeks 8] [--json]

Power zones are Coggan % of FTP; time coasting (0 W) is excluded and reported separately.
3-zone model: low = Z1-Z2 (<75% FTP), mid = Z3-Z4 (75-105%), high = Z5+ (>105%).
"""
import datetime as dt
from collections import defaultdict

from . import data, derive, metrics as m
from .report import emit, hours, parser, table, week_start


def distribution(rows):
    pz, hz = [0] * 7, [0] * 5
    coasting = moving = 0
    for r in rows:
        if r["power_zone_s"]:
            pz = [a + b for a, b in zip(pz, r["power_zone_s"])]
            coasting += r["coasting_s"] or 0
            moving += r["moving_s"]
        if r["hr_zone_s"]:
            hz = [a + b for a, b in zip(hz, r["hr_zone_s"])]
    total_p, total_h = sum(pz), sum(hz)
    low, mid, high = m.three_zone(pz) if total_p else (None, None, None)
    return {
        "power_zone_pct": [round(x / total_p, 3) for x in pz] if total_p else None,
        "hr_zone_pct": [round(x / total_h, 3) for x in hz] if total_h else None,
        "pedaling_s": total_p, "coasting_pct": round(coasting / moving, 3) if moving else None,
        "low": low and round(low, 3), "mid": mid and round(mid, 3), "high": high and round(high, 3),
        "model": m.distribution_model(low, mid, high) if total_p else None,
    }


def by_period(rows, by="week", n=8):
    today = data.today()
    key = (lambda d: week_start(d).isoformat()) if by == "week" else (lambda d: d.isoformat()[:7])
    groups = defaultdict(list)
    for r in rows:
        if r["cycling"]:
            groups[key(data.parse_day(r["date"]))].append(r)
    if by == "week":
        periods = [(week_start(today) - dt.timedelta(weeks=i)).isoformat() for i in range(n - 1, -1, -1)]
    else:
        periods = sorted(groups)[-n:]
    return [{"period": p, **distribution(groups.get(p, []))} for p in periods]


def render(res):
    lines = [table(
        ["Period", "Pedaling", "Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Low/Mid/High", "Model", "Coasting"],
        [[p["period"], hours(p["pedaling_s"])] + ([f"{x:.0%}" for x in p["power_zone_pct"]]
                                                   if p["power_zone_pct"] else [""] * 7)
         + [f"{p['low']:.0%}/{p['mid']:.0%}/{p['high']:.0%}" if p["low"] is not None else "", p["model"],
            f"{p['coasting_pct']:.0%}" if p["coasting_pct"] is not None else ""]
         for p in res["periods"]])]
    t = res["total"]
    lines += ["", f"**Whole period**: low {t['low']:.0%} / mid {t['mid']:.0%} / high {t['high']:.0%} "
                  f"-> {t['model']}. HR zones: "
              + ", ".join(f"Z{i + 1} {x:.0%}" for i, x in enumerate(t["hr_zone_pct"] or []))]
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    p.add_argument("--by", choices=("week", "month"), default="week")
    p.add_argument("--weeks", type=int, default=8, help="number of periods")
    args = p.parse_args()
    rows = derive.ensure()
    periods = by_period(rows, args.by, args.weeks)
    first = periods[0]["period"]
    start = data.parse_day(first if args.by == "week" else first + "-01")
    total = distribution([r for r in rows if r["cycling"] and data.parse_day(r["date"]) >= start])
    emit({"periods": periods, "total": total}, args.json, render)


if __name__ == "__main__":
    main()
