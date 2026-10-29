"""Plan the next days of training from current load, recent key sessions and goals.

  python3 -m coach.plan_workouts [--start YYYY-MM-DD] [--days 7] [--focus threshold] [--json]

- Weekly TSS target raises CTL by plan.target_ctl_ramp_per_week (coach_config.json);
  every plan.recovery_week_every-th week, or when form is below -25, it plans a recovery week.
- Key sessions progress from the last session of the same type: if it was executed at
  target intensity, the next one adds work time; otherwise it repeats the same volume.
- Availability per weekday comes from coach_config.json or, if unset, from the last 8 weeks.
- --json includes `icu_events`, ready for intervals.icu bulk event creation.
"""
import datetime as dt

from . import data, derive, metrics as m, training_load
from .report import emit, parser, table

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WARMUP = ("Warmup", 1, [(900, 50, 75, True)])
COOLDOWN = ("Cooldown", 1, [(600, 50, 60, False)])
# (reps, work s, rest s, low %FTP, high %FTP)
LADDERS = {
    "tempo": [(2, 1200, 300, 80, 88), (3, 1200, 300, 80, 88), (2, 1800, 300, 80, 88), (2, 2400, 300, 80, 87)],
    "sweetspot": [(3, 720, 240, 88, 93), (2, 1200, 300, 88, 93), (3, 900, 300, 88, 93), (3, 1200, 300, 88, 93),
                  (2, 1800, 300, 88, 92)],
    "threshold": [(3, 600, 300, 95, 100), (3, 720, 300, 97, 102), (4, 600, 300, 97, 102), (3, 900, 300, 95, 100),
                  (4, 720, 240, 97, 102), (3, 1200, 300, 95, 100)],
    "vo2max": [(5, 180, 180, 110, 118), (4, 240, 240, 108, 115), (5, 240, 240, 108, 115),
               (6, 240, 240, 106, 112), (5, 300, 300, 105, 110)],
    "anaerobic": [(6, 30, 270, 140, 160), (8, 30, 270, 140, 160), (6, 60, 300, 125, 135)],
}
CATEGORY_FLOOR = {"tempo": 0.76, "sweetspot": 0.88, "threshold": 0.95, "vo2max": 1.05, "anaerobic": 1.20}
FOCUS_KEYS = {"threshold": ["threshold", "vo2max"], "vo2max": ["vo2max", "threshold"],
              "sweetspot": ["sweetspot", "threshold"], "base": ["tempo", "sweetspot"]}
ENDURANCE = (60, 72)
RECOVERY = (50, 60)


def fmt_step_dur(sec):
    return f"{sec // 60}m" if sec % 60 == 0 else f"{sec}s"


def workout_text(blocks):
    parts = []
    for header, reps, steps in blocks:
        lines = [header + (f" {reps}x" if reps > 1 else "")]
        for sec, lo, hi, ramp in steps:
            target = f"{lo}-{hi}%" if lo != hi else f"{lo}%"
            lines.append(f"- {fmt_step_dur(sec)} {'ramp ' if ramp else ''}{target}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def blocks_tss(blocks):
    total = 0.0
    for _, reps, steps in blocks:
        for sec, lo, hi, ramp in steps:
            if_sq = ((lo ** 2 + lo * hi + hi ** 2) / 3 if ramp else ((lo + hi) / 2) ** 2) / 10000
            total += reps * sec * if_sq / 36
    return total


def blocks_seconds(blocks):
    return sum(reps * sum(s[0] for s in steps) for _, reps, steps in blocks)


def rung_work(rung):
    return rung[0] * rung[1]


def interval_session(cat, rung, extra_min=0):
    reps, work, rest, lo, hi = rung
    blocks = [WARMUP, (f"Main {cat}", reps, [(work, lo, hi, False), (rest, 50, 55, False)])]
    if extra_min:
        blocks.append(("Endurance", 1, [(extra_min * 60, *ENDURANCE, False)]))
    blocks.append(COOLDOWN)
    name = f"{reps}x{fmt_step_dur(work)} {cat} @ {lo}-{hi}%"
    return name, blocks


def steady_session(minutes, band, label):
    return f"{label} {minutes // 60}h{minutes % 60:02d}" if minutes >= 60 else f"{label} {minutes}m", \
        [("Main", 1, [(minutes * 60, *band, False)])]


def last_session(rows, cat, before, days=60):
    start = before - dt.timedelta(days=days)
    for r in reversed(rows):
        d = data.parse_day(r["date"])
        if start <= d < before and r["structured"] and (r["category"] == cat or cat in r["secondary"]):
            work = [e for e in r["efforts"] if e.get("set") is not None and e["category"] == cat]
            if work:
                return {"date": r["date"], "name": r["name"], "work_s": sum(e["duration"] for e in work),
                        "pct": m.mean([e["pct_ftp"] for e in work])}
    return None


def next_rung(cat, last, recovery_week=False):
    ladder = sorted(LADDERS[cat], key=rung_work)
    if recovery_week or not last:
        return ladder[0], "first rung" if not last else "recovery week: lightest rung"
    if last["pct"] >= CATEGORY_FLOOR[cat]:
        for rung in ladder:
            if rung_work(rung) > last["work_s"] * 1.05:
                return rung, f"progressing from {m.fmt_dur(last['work_s'])} of work on {last['date']}"
        return ladder[-1], "top of ladder"
    rung = min(ladder, key=lambda r: abs(rung_work(r) - last["work_s"]))
    return rung, f"repeating volume: {last['date']} was below target ({last['pct']:.0%} FTP)"


def availability(rows, today):
    cfg = data.config()["plan"]["availability_min"]
    if cfg:
        return {d: cfg.get(d, 0) for d in WEEKDAYS}, "coach_config.json"
    start = today - dt.timedelta(weeks=8)
    per_day = {}
    for r in rows:
        d = data.parse_day(r["date"])
        if r["cycling"] and start <= d < today:
            per_day[d] = per_day.get(d, 0) + r["moving_s"] / 60
    out = {}
    for i, name in enumerate(WEEKDAYS):
        mins = sorted(v for d, v in per_day.items() if d.weekday() == i)
        out[name] = int(round(mins[len(mins) // 2] / 15) * 15) if len(mins) >= 3 else 0
    return out, "inferred from the last 8 weeks"


def is_recovery_week(rows, today, state):
    every = data.config()["plan"]["recovery_week_every"]
    weeks = training_load.weekly(rows, 8, today - dt.timedelta(days=1))
    build = 0
    for w in reversed([w for w in weeks if not w["partial"]]):
        if "recovery/low week" in w["flags"]:
            break
        build += 1
    if state["tsb_after"] < -25:
        return True, f"form is {state['tsb_after']} (< -25)"
    if build >= every - 1:
        return True, f"{build} build weeks since the last recovery week"
    return False, f"{build} build week(s) since the last recovery week"


def key_categories(focus, today):
    goals = data.config()["goals"]
    if focus == "event" and goals.get("event_date"):
        weeks_out = (data.parse_day(goals["event_date"]) - today).days / 7
        if weeks_out > 12:
            return ["sweetspot", "tempo"], f"event in {weeks_out:.0f} weeks: base"
        if weeks_out > 3:
            return ["threshold", "vo2max"], f"event in {weeks_out:.0f} weeks: build"
        return ["vo2max", "threshold"], f"event in {weeks_out:.1f} weeks: sharpen/taper"
    return FOCUS_KEYS.get(focus, FOCUS_KEYS["threshold"]), f"focus: {focus}"


def pick_key_days(days, avail, n):
    cands = [d for d in days if avail[d] >= 75]
    longest = max(cands, key=lambda d: avail[d], default=None)
    order = [d for d in cands if d != longest] + ([longest] if longest else [])
    chosen = []
    for strict in (True, False):
        for d in order:
            if len(chosen) == n:
                break
            if d in chosen:
                continue
            if strict and any(abs((d - c).days) <= 1 for c in chosen):
                continue
            chosen.append(d)
    return sorted(chosen)


def plan(rows, start, n_days=7, focus=None):
    cfg = data.config()
    focus = focus or cfg["goals"]["focus"]
    state = training_load.state(rows, start - dt.timedelta(days=1))
    recovery, why_recovery = is_recovery_week(rows, start, state)
    ramp = cfg["plan"]["target_ctl_ramp_per_week"]
    target = round(0.6 * 7 * state["ctl"]) if recovery else round(7 * m.daily_load_for_ramp(state["ctl"], ramp))
    target = round(target * n_days / 7)
    avail_by_name, avail_src = availability(rows, start)
    days = [start + dt.timedelta(days=i) for i in range(n_days)]
    avail = {d: avail_by_name[WEEKDAYS[d.weekday()]] for d in days}
    cats, why_cats = key_categories(focus, start)
    n_keys = 1 if recovery else cfg["plan"]["key_sessions_per_week"]
    key_days = pick_key_days(days, avail, n_keys)

    sessions = {}
    for i, d in enumerate(key_days):
        cat = cats[i % len(cats)]
        last = last_session(rows, cat, start)
        rung, why = next_rung(cat, last, recovery)
        name, blocks = interval_session(cat, rung)
        sessions[d] = {"kind": "key", "category": cat, "rung": rung, "name": name, "blocks": blocks, "why": why}

    remaining = target - sum(blocks_tss(s["blocks"]) for s in sessions.values())
    other = [d for d in days if d not in sessions and avail[d] >= 45]
    per_hour = blocks_tss([("x", 1, [(3600, *ENDURANCE, False)])])
    total_avail = sum(avail[d] for d in other) or 1
    for d in other:
        after_key = (d - dt.timedelta(days=1)) in key_days
        if recovery and after_key:
            minutes, band, label, kind = 60, RECOVERY, "Recovery", "recovery"
        else:
            cap = min(avail[d], 90) if recovery else avail[d]
            minutes = int(max(45, min(cap, remaining * avail[d] / total_avail / per_hour * 60)) // 15 * 15)
            band, kind = ENDURANCE, "endurance"
            label = "Long endurance" if minutes >= 180 else "Endurance"
        name, blocks = steady_session(minutes, band, label)
        sessions[d] = {"kind": kind, "category": "endurance", "name": name, "blocks": blocks,
                       "why": "easy day after key session" if kind == "recovery" else f"{avail[d]} min available"}
    # Use leftover budget to extend key days with endurance riding, within availability.
    leftover = target - sum(blocks_tss(s["blocks"]) for s in sessions.values())
    for d in key_days:
        s = sessions[d]
        spare = avail[d] - blocks_seconds(s["blocks"]) // 60
        if leftover > 10 and spare >= 30 and not recovery:
            extra = int(min(spare, leftover / per_hour * 60) // 15 * 15)
            if extra >= 15:
                name, blocks = interval_session(s["category"], s["rung"], extra)
                leftover -= blocks_tss(blocks) - blocks_tss(s["blocks"])
                s["blocks"], s["name"] = blocks, name + f" + {extra}m endurance"

    out_days = []
    for d in days:
        s = sessions.get(d)
        if not s:
            out_days.append({"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "kind": "rest",
                             "name": "Rest", "available_min": avail[d]})
            continue
        out_days.append({
            "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "kind": s["kind"], "category": s["category"],
            "name": s["name"], "duration_min": blocks_seconds(s["blocks"]) // 60,
            "est_tss": round(blocks_tss(s["blocks"])), "available_min": avail[d], "why": s["why"],
            "description": workout_text(s["blocks"]),
        })
    planned = sum(x.get("est_tss", 0) for x in out_days)
    return {
        "start": start.isoformat(), "days": out_days, "focus": focus, "ftp": data.ftp_on(start.isoformat()),
        "week_type": "recovery" if recovery else "build", "why_week_type": why_recovery,
        "key_categories": cats, "why_key_categories": why_cats,
        "ctl": state["ctl"], "tsb": state["tsb_after"], "target_tss": target, "planned_tss": planned,
        "expected_ctl_change": round((planned / n_days - state["ctl"]) * (1 - (41 / 42) ** n_days), 1),
        "availability": avail_by_name, "availability_source": avail_src,
        "icu_events": [{"start_date_local": f"{x['date']}T00:00:00", "name": x["name"], "category": "WORKOUT",
                        "event_type": "Ride", "description": x["description"],
                        "duration_seconds": x["duration_min"] * 60, "training_load": x["est_tss"]}
                       for x in out_days if x["kind"] != "rest"],
    }


def render(p):
    lines = [f"**{p['week_type'].title()} block from {p['start']}** ({p['why_week_type']}). "
             f"CTL {p['ctl']}, form {p['tsb']}. Target {p['target_tss']} TSS, planned {p['planned_tss']} TSS "
             f"(CTL {p['expected_ctl_change']:+}). Key sessions: {', '.join(p['key_categories'])} "
             f"({p['why_key_categories']}). FTP {p['ftp']} W. Availability {p['availability_source']}.", "",
             table(["Date", "Day", "Session", "Time", "TSS", "Why"],
                   [[d["date"], d["weekday"], d["name"], m.fmt_dur(d.get("duration_min", 0) * 60) if d.get("duration_min") else "",
                     d.get("est_tss", ""), d.get("why", "")] for d in p["days"]])]
    for d in p["days"]:
        if d["kind"] == "key":
            lines += ["", f"**{d['date']} {d['name']}**", "```", d["description"], "```"]
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    p.add_argument("--start", help="first day (default tomorrow)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--focus", choices=sorted(FOCUS_KEYS) + ["event"])
    args = p.parse_args()
    start = data.parse_day(args.start) if args.start else data.today() + dt.timedelta(days=1)
    emit(plan(derive.ensure(), start, args.days, args.focus), args.json, render)


if __name__ == "__main__":
    main()
