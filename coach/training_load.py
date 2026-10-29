"""Training load: daily TSS -> fitness (CTL), fatigue (ATL), form (TSB), ramp, monotony.

  python3 -m coach.training_load [--weeks 8] [--json]

CTL/ATL are 42/7-day exponential averages of daily TSS; TSB = yesterday's CTL - ATL.
The chart is seeded with the average daily load of the first 28 days of data, so
the first weeks are approximate.
"""
import datetime as dt
import statistics
from collections import defaultdict

from . import data, derive, metrics as m
from .report import emit, hours, parser, table, week_start

FORM_BANDS = ((5, "fresh"), (-10, "neutral"), (-30, "productive fatigue"), (float("-inf"), "overreached"))


def form_label(tsb):
    return next(label for bound, label in FORM_BANDS if tsb > bound)


def daily_tss(rows):
    out = defaultdict(float)
    for r in rows:
        out[data.parse_day(r["date"])] += r["tss"] or 0
    return out


def pmc(rows, until=None):
    """[{date, tss, ctl, atl, tsb}] for every day from the first activity to `until` (default today)."""
    loads = daily_tss(rows)
    if not loads:
        return []
    first, until = min(loads), until or data.today()
    seed = sum(loads.get(first + dt.timedelta(days=i), 0) for i in range(28)) / 28
    ctl = atl = seed
    out, day = [], first
    while day <= until:
        tsb = ctl - atl
        ctl, atl = m.pmc_step(ctl, atl, loads.get(day, 0))
        out.append({"date": day.isoformat(), "tss": round(loads.get(day, 0), 1),
                    "ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(tsb, 1)})
        day += dt.timedelta(days=1)
    return out


def state(rows, on=None):
    """PMC values for one day (default today)."""
    days = pmc(rows, on)
    if not days:
        return None
    today = days[-1]
    week_ago = days[-8] if len(days) >= 8 else days[0]
    after = round(today["ctl"] - today["atl"], 1)
    return {**today, "tsb_after": after, "ramp_7d": round(today["ctl"] - week_ago["ctl"], 1),
            "form": form_label(today["tsb"]), "form_after": form_label(after)}


def weekly(rows, n_weeks=8, until=None):
    until = until or data.today()
    days = {d["date"]: d for d in pmc(rows, until)}
    by_week = defaultdict(list)
    for r in rows:
        by_week[week_start(data.parse_day(r["date"]))].append(r)
    out = []
    for i in range(n_weeks - 1, -1, -1):
        start = week_start(until) - dt.timedelta(weeks=i)
        end = min(start + dt.timedelta(days=6), until)
        wk = by_week.get(start, [])
        daily = [days.get((start + dt.timedelta(days=k)).isoformat(), {}).get("tss", 0) for k in range(7)]
        sd = statistics.pstdev(daily)
        end_day, prev_end = days.get(end.isoformat()), days.get((start - dt.timedelta(days=1)).isoformat())
        tss = round(sum(daily), 1)
        partial = end < start + dt.timedelta(days=6)
        monotony = round(statistics.mean(daily) / sd, 2) if sd and not partial else None
        out.append({
            "week": start.isoformat(), "partial": partial,
            "sessions": len(wk), "rides": sum(r["cycling"] for r in wk),
            "moving_s": sum(r["moving_s"] for r in wk),
            "distance_km": round(sum(r["distance_km"] for r in wk)), "kj": sum(r["kj"] or 0 for r in wk),
            "tss": tss, "ctl": end_day and end_day["ctl"], "atl": end_day and end_day["atl"],
            "tsb": end_day and round(end_day["ctl"] - end_day["atl"], 1),
            "ramp": end_day and prev_end and round(end_day["ctl"] - prev_end["ctl"], 1),
            "monotony": monotony, "strain": round(tss * monotony) if monotony else None,
        })
    for i, w in enumerate(out):
        prior = [x["tss"] for x in out[max(0, i - 3):i]]
        w["flags"] = flags(w, statistics.mean(prior) if len(prior) == 3 else None)
    return out


def flags(w, prior_avg):
    f = []
    if w["ramp"] is not None and w["ramp"] > 8:
        f.append("fast ramp (>8 CTL/wk)")
    if w["tsb"] is not None and w["tsb"] < -30:
        f.append("deep fatigue (TSB<-30)")
    if w["monotony"] and w["monotony"] > 2:
        f.append("monotonous (>2)")
    if prior_avg and not w["partial"] and w["tss"] < 0.7 * prior_avg:
        f.append("recovery/low week")
    return f


def render(res):
    s = res["today"]
    lines = [f"**Today ({s['date']})**: fitness CTL {s['ctl']}, fatigue ATL {s['atl']}, "
             f"CTL ramp {s['ramp_7d']:+} over 7 days. Form TSB {s['tsb']} this morning ({s['form']}), "
             f"{s['tsb_after']} after today's training ({s['form_after']}).", ""]
    lines.append(table(
        ["Week", "Rides", "Time", "km", "TSS", "CTL", "ATL", "TSB at end", "Ramp", "Monotony", "Flags"],
        [[w["week"] + (" (so far)" if w["partial"] else ""), w["rides"], hours(w["moving_s"]), w["distance_km"],
          round(w["tss"]), w["ctl"], w["atl"], w["tsb"], w["ramp"], w["monotony"], ", ".join(w["flags"])]
         for w in res["weeks"]]))
    lines.append("\nTSS source per activity: power (1 Hz streams), device (head-unit TSS for coarse "
                 "recordings), hr (hrTSS), duration (estimate for activities without power or HR).")
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    p.add_argument("--weeks", type=int, default=8)
    args = p.parse_args()
    rows = derive.ensure()
    emit({"today": state(rows), "weeks": weekly(rows, args.weeks)}, args.json, render)


if __name__ == "__main__":
    main()
