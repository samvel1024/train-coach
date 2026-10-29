"""Weekly coaching review: volume vs recent weeks, sessions, load, intensity mix, key-session
execution, aerobic markers, new bests and flags.

  python3 -m coach.weekly_review [--week YYYY-MM-DD | --last] [--json]

--week takes any date in the target week (Mon-Sun). Default: the current week so far.
"""
import datetime as dt

from . import aerobic, analyse_session, data, derive, intensity, metrics as m, power_profile, training_load
from .classify import sets_text
from .report import emit, hours, parser, rows_between, table, week_start


def totals(rows):
    return {"sessions": len(rows), "moving_s": sum(r["moving_s"] for r in rows),
            "distance_km": round(sum(r["distance_km"] for r in rows)), "tss": round(sum(r["tss"] or 0 for r in rows)),
            "kj": sum(r["kj"] or 0 for r in rows)}


def week_prs(rows, start, end):
    week = power_profile.bests(rows, start, end)
    before = power_profile.bests(rows, start - dt.timedelta(days=365), start - dt.timedelta(days=1))
    return [{"duration": d, **v, "previous": before.get(d, {}).get("watts")}
            for d, v in sorted(week.items(), key=lambda x: int(x[0]))
            if d not in before or v["watts"] > before[d]["watts"]]


def suggestions(review):
    s, load, dist = [], review["load_end"], review["intensity"]
    if load["tsb_after"] < -30:
        s.append("Form is below -30: schedule 2-3 easy days before the next key session.")
    elif load["tsb_after"] > 10:
        s.append("Form is high (>10): there is room for a harder week or a test.")
    if review["ramp"] is not None and review["ramp"] > 8:
        s.append(f"Fitness rose {review['ramp']:+} CTL this week; above ~8/week the injury/illness risk grows.")
    keys = [x for x in review["sessions"] if x["category"] in ("threshold", "vo2max", "sweetspot", "anaerobic", "race")]
    if not keys and not review["partial"]:
        s.append("No key session this week: intensity will drift unless that was a planned recovery week.")
    if dist["mid"] is not None and dist["mid"] > 0.35:
        s.append(f"{dist['mid']:.0%} of pedaling time was tempo/threshold: watch for too much grey-zone riding.")
    for k in review["key_sessions"]:
        for st in k["sets"]:
            if st["fade_pct"] < -8:
                s.append(f"{k['date']} {k['name']}: power faded {st['fade_pct']}% across reps; targets may be "
                         f"too high or recovery between reps too short.")
        for c in k["compliance"]:
            if c["verdict"] != "completed":
                s.append(f"{k['date']} {k['name']}: plan {c['reps']}x{m.fmt_dur(c['duration'])} {c['verdict']}.")
    for r in review["aerobic"]:
        if r["decoupling_pct"] is not None and r["decoupling_pct"] > 5 and r["moving_s"] >= 2 * 3600:
            s.append(f"{r['date']}: {r['decoupling_pct']}% decoupling on a {m.fmt_dur(r['moving_s'])} "
                     f"endurance ride; aerobic durability is a limiter (fueling/heat can also cause this).")
    return s


def review(rows, start):
    end = min(start + dt.timedelta(days=6), data.today())
    week = rows_between(rows, start, end)
    prior = [totals(rows_between(rows, start - dt.timedelta(weeks=i), start - dt.timedelta(weeks=i) + dt.timedelta(days=6)))
             for i in range(1, 5)]
    prior_avg = {k: round(m.mean([p[k] for p in prior])) for k in prior[0]}
    load_end = training_load.state(rows, end)
    load_start = training_load.state(rows, start - dt.timedelta(days=1))
    keys = []
    for r in week:
        if r["structured"] and r["category"] in ("threshold", "vo2max", "sweetspot", "tempo", "anaerobic"):
            a = analyse_session.analyse(rows, r)
            keys.append({"id": r["id"], "date": r["date"], "name": r["name"], "category": r["category"],
                         "work": sets_text(r), "sets": a["sets"], "compliance": a["compliance"]})
    res = {
        "week": start.isoformat(), "end": end.isoformat(), "partial": end < start + dt.timedelta(days=6),
        "totals": totals(week), "prior_4wk_avg": prior_avg,
        "sessions": [{"id": r["id"], "date": r["date"], "name": r["name"], "category": r["category"],
                      "moving_s": r["moving_s"], "tss": r["tss"], "if": r["if"], "work": sets_text(r)} for r in week],
        "load_end": load_end, "ramp": round(load_end["ctl"] - load_start["ctl"], 1) if load_start else None,
        "intensity": intensity.distribution([r for r in week if r["cycling"]]),
        "key_sessions": keys,
        "aerobic": [x for x in aerobic.analyse(rows, start)["rides"] if x["date"] <= end.isoformat()],
        "prs": week_prs(rows, start, end),
    }
    res["suggestions"] = suggestions(res)
    return res


def render(r):
    t, p, ld = r["totals"], r["prior_4wk_avg"], r["load_end"]
    lines = [f"# Week of {r['week']}" + (f" (through {r['end']})" if r["partial"] else ""),
             f"**{t['sessions']} sessions, {hours(t['moving_s'])}, {t['distance_km']} km, {t['tss']} TSS** "
             f"(previous 4-week average: {hours(p['moving_s'])}, {p['distance_km']} km, {p['tss']} TSS)",
             f"Load at end: CTL {ld['ctl']} ({r['ramp']:+} this week), ATL {ld['atl']}, "
             f"TSB {ld['tsb_after']} ({ld['form_after']})", "",
             table(["Date", "Session", "Type", "Time", "IF", "TSS", "Work"],
                   [[s["date"], (s["name"] or "")[:28], s["category"], m.fmt_dur(s["moving_s"]), s["if"],
                     round(s["tss"] or 0), s["work"]] for s in r["sessions"]])]
    d = r["intensity"]
    if d["low"] is not None:
        lines += ["", f"**Intensity**: low {d['low']:.0%} / mid {d['mid']:.0%} / high {d['high']:.0%} ({d['model']})"]
    for k in r["key_sessions"]:
        lines += ["", f"**{k['date']} {k['name']}** ({k['category']}): {k['work']}"]
        lines += [f"- set of {s['reps']}x{m.fmt_dur(s['avg_duration'])} @ {s['avg_power']} W: fade {s['fade_pct']:+}%, "
                  f"HR drift {s['hr_drift_bpm']:+} bpm" if s["hr_drift_bpm"] is not None else
                  f"- set of {s['reps']}x{m.fmt_dur(s['avg_duration'])} @ {s['avg_power']} W: fade {s['fade_pct']:+}%"
                  for s in k["sets"]]
        lines += [f"- plan {c['reps']}x{m.fmt_dur(c['duration'])}: {c['verdict']}" for c in k["compliance"]]
    if r["aerobic"]:
        lines += ["", "**Endurance rides**: " + "; ".join(
            f"{a['date']} EF {a['ef']}" + (f", decoupling {a['decoupling_pct']}%" if a["decoupling_pct"] is not None else "")
            for a in r["aerobic"])]
    if r["prs"]:
        lines += ["", "**New bests**: " + ", ".join(
            f"{power_profile.LABELS.get(x['duration'], x['duration'])} {x['watts']:.0f} W" for x in r["prs"])]
    lines += ["", "## Coach notes"] + ([f"- {s}" for s in r["suggestions"]] or ["- Nothing flagged."])
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    p.add_argument("--week", help="any date in the week")
    p.add_argument("--last", action="store_true", help="previous full week")
    args = p.parse_args()
    day = data.parse_day(args.week) if args.week else data.today() - dt.timedelta(weeks=1 if args.last else 0)
    emit(review(derive.ensure(), week_start(day)), args.json, render)


if __name__ == "__main__":
    main()
