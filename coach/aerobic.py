"""Aerobic fitness on endurance rides: efficiency factor (NP/HR) and Pa:HR decoupling.

  python3 -m coach.aerobic [--days 180] [--min-minutes 60] [--json]

Rising EF at the same intensity = better aerobic fitness. Decoupling < 5% on a long
steady ride = good aerobic durability. Indoor and outdoor are trended separately
(different power sources, no cooling/terrain indoors).
"""
from collections import defaultdict

from . import data, derive, metrics as m
from .report import emit, parser, since_arg, since_date, table


def endurance_rides(rows, start, min_minutes=60):
    return [r for r in rows
            if r["category"] == "endurance" and r["has_power"] and r["has_hr"] and r["ef"]
            and r["moving_s"] >= min_minutes * 60 and data.parse_day(r["date"]) >= start]


def trend(rides):
    """EF change per 30 days (linear fit) and monthly averages."""
    if not rides:
        return {"ef_per_30d": None, "monthly": {}}
    t0 = data.parse_day(rides[0]["date"])
    xs = [(data.parse_day(r["date"]) - t0).days for r in rides]
    slope = m.linear_slope(xs, [r["ef"] for r in rides])
    monthly = defaultdict(list)
    for r in rides:
        monthly[r["date"][:7]].append(r)
    return {
        "ef_per_30d": round(slope * 30, 4) if slope is not None else None,
        "monthly": {mon: {"rides": len(rs), "ef": round(m.mean([r["ef"] for r in rs]), 3),
                          "decoupling_pct": round(m.mean(d), 1) if (d := [r["decoupling_pct"] for r in rs
                                                                          if r["decoupling_pct"] is not None]) else None,
                          "if": round(m.mean([r["if"] for r in rs]), 3)}
                    for mon, rs in sorted(monthly.items())},
    }


def analyse(rows, start, min_minutes=60):
    rides = endurance_rides(rows, start, min_minutes)
    out = {"rides": [{k: r[k] for k in ("id", "date", "name", "indoor", "moving_s", "np", "if", "avg_hr",
                                         "ef", "decoupling_pct", "low_res")} for r in rides]}
    for env, flag in (("outdoor", False), ("indoor", True)):
        out[env] = trend([r for r in rides if r["indoor"] == flag])
    return out


def render(res):
    lines = []
    for env in ("outdoor", "indoor"):
        t = res[env]
        if not t["monthly"]:
            continue
        slope = t["ef_per_30d"]
        lines += [f"## {env.title()} endurance rides",
                  f"EF trend: {slope:+.3f} per 30 days" if slope is not None else "EF trend: n/a",
                  table(["Month", "Rides", "Avg IF", "EF (NP/HR)", "Decoupling"],
                        [[mon, v["rides"], v["if"], v["ef"],
                          f"{v['decoupling_pct']}%" if v["decoupling_pct"] is not None else ""]
                         for mon, v in t["monthly"].items()]), ""]
    recent = res["rides"][-8:]
    lines += ["## Most recent endurance rides", table(
        ["Date", "Ride", "Where", "Time", "IF", "Avg HR", "EF", "Decoupling"],
        [[r["date"], (r["name"] or "")[:30], "indoor" if r["indoor"] else "outdoor", m.fmt_dur(r["moving_s"]),
          r["if"], r["avg_hr"], r["ef"],
          (f"{r['decoupling_pct']}%" + (" (high)" if r["decoupling_pct"] > 5 else ""))
          if r["decoupling_pct"] is not None else ""] for r in recent])]
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    since_arg(p, 180)
    p.add_argument("--min-minutes", type=int, default=60)
    args = p.parse_args()
    emit(analyse(derive.ensure(), since_date(args), args.min_minutes), args.json, render)


if __name__ == "__main__":
    main()
