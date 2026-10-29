"""Deep dive into one session: intervals, fade, HR drift/recovery, W' balance, plan compliance.

  python3 -m coach.analyse_session [ID | latest] [--date YYYY-MM-DD] [--plan "4x10min@320W"] [--json]

--plan accepts comma-separated parts like "4x10min@320W", "3x15m@95%", "13x30s@120%".
Without --plan, the activity name is parsed the same way (e.g. "4x10min").
"""
import re

from . import data, derive, metrics as m, power_profile
from .report import emit, parser, table

PLAN_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(s|sec|m|min|h)\b\s*(?:@\s*(\d+(?:\.\d+)?)\s*(w|%))?", re.I)


def parse_plan(text, ftp):
    parts = []
    for n, dur, unit, target, kind in PLAN_RE.findall(text or ""):
        sec = float(dur) * {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}[unit.lower()]
        watts = None
        if target:
            watts = float(target) if kind.lower() == "w" else float(target) / 100 * ftp
        parts.append({"reps": int(n), "duration": round(sec), "target_w": watts and round(watts)})
    return parts


def select(rows, ident=None, date=None):
    if date:
        hits = [r for r in rows if r["date"] == date]
        if not hits:
            raise SystemExit(f"no activity on {date}")
        return max(hits, key=lambda r: r["moving_s"])
    if ident and ident != "latest":
        hits = [r for r in rows if str(r["id"]) == str(ident)]
        if not hits:
            raise SystemExit(f"activity {ident} not in dataset (run the sync skill?)")
        return hits[0]
    return rows[-1]


def hr_recovery(hr, end, after=60):
    """HR drop in the `after` seconds following an interval."""
    if not hr or end + after >= len(hr) or not hr[end - 1] or not hr[end + after]:
        return None
    return hr[end - 1] - hr[end + after]


def blocks(reps):
    """Split a set into blocks at recoveries much longer than usual (e.g. 3 sets of 13x30/15s)."""
    if len(reps) < 6 or any(e["start"] is None for e in reps):
        return [reps]
    gaps = [b["start"] - (a["start"] + a["duration"]) for a, b in zip(reps, reps[1:])]
    typical = sorted(gaps)[len(gaps) // 2]
    out = [[reps[0]]]
    for gap, e in zip(gaps, reps[1:]):
        if gap > max(180, 1.5 * typical):
            out.append([])
        out[-1].append(e)
    return out


def set_analysis(efforts):
    sets = {}
    for e in efforts:
        if e.get("set") is not None:
            sets.setdefault(e["set"], []).append(e)
    out = []
    for set_id, reps in sets.items():
        for block in blocks(sorted(reps, key=lambda e: e["start"] or 0)):
            if len(block) < 2:
                continue
            out.append({"set": set_id, **rep_stats(block)})
    return out


def rep_stats(reps):
    powers = [e["avg_power"] for e in reps]
    first, last = reps[0], reps[-1]
    has_hr = bool(first["avg_hr"] and last["avg_hr"])
    mu = m.mean(powers)
    cv = (sum((p - mu) ** 2 for p in powers) / len(powers)) ** 0.5 / mu
    return {
        "reps": len(reps), "avg_duration": round(m.mean([e["duration"] for e in reps])),
        "avg_power": round(mu), "pct_ftp": round(m.mean([e["pct_ftp"] for e in reps]), 3),
        "category": first["category"],
        "fade_pct": round((last["avg_power"] - first["avg_power"]) / first["avg_power"] * 100, 1),
        "power_cv_pct": round(cv * 100, 1),
        "hr_drift_bpm": last["avg_hr"] - first["avg_hr"] if has_hr else None,
        "watts_per_beat_change_pct": round(((last["avg_power"] / last["avg_hr"])
                                            / (first["avg_power"] / first["avg_hr"]) - 1) * 100, 1)
        if has_hr else None,
    }


def compliance(plan, efforts):
    """Match each planned part against work efforts of roughly that length (50-150%)."""
    out = []
    for part in plan:
        d = part["duration"]
        reps = [e for e in efforts if e.get("set") is not None and 0.5 * d <= e["duration"] <= 1.5 * d]
        if not reps:
            out.append({**part, "verdict": "not found"})
            continue
        full = [e for e in reps if e["duration"] >= 0.9 * d]
        avg_w = round(m.mean([e["avg_power"] for e in reps]))
        res = {**part, "done_reps": len(reps), "full_reps": len(full),
               "short_reps": [e["duration"] for e in reps if e["duration"] < 0.9 * d],
               "done_power": avg_w,
               "power_vs_target_pct": round((avg_w / part["target_w"] - 1) * 100, 1) if part["target_w"] else None}
        ok_pow = part["target_w"] is None or avg_w >= 0.97 * part["target_w"]
        res["verdict"] = ("completed" if len(full) >= part["reps"] and ok_pow else
                          "partially completed" if len(reps) >= 1 else "not found")
        out.append(res)
    return out


def analyse(rows, row, plan_text=None):
    rec = data.load_raw(row["id"])
    series, segments, _, _ = derive.series_1hz(rec)
    power, hr = series.get("watts"), series.get("heartrate")
    efforts = [dict(e) for e in row["efforts"]]
    for e in efforts:
        if e["start"] is not None:
            e["hr_recovery_60s"] = hr_recovery(hr, e["start"] + e["duration"])
    prior = [r for r in rows if r["date"] < row["date"]]
    est = power_profile.estimates(prior, data.parse_day(row["date"]))
    wbal = None
    # Holding 4-8 s samples overstates time above CP, so W' balance is skipped on coarse files.
    if power and not row["low_res"] and est["cp"] and est["w_prime_j"] and est["w_prime_j"] > 0:
        bal = m.w_prime_balance(power, est["cp"], est["w_prime_j"])
        low = min(range(len(bal)), key=bal.__getitem__)
        wbal = {"cp": est["cp"], "w_prime_j": est["w_prime_j"], "min_j": round(bal[low]),
                "min_pct": round(bal[low] / est["w_prime_j"], 3), "at_s": low}
    sets = set_analysis(efforts)
    plan = parse_plan(plan_text or row["name"], row["ftp"])
    return {
        "activity": {k: row[k] for k in ("id", "date", "name", "sport_type", "category", "secondary", "moving_s",
                                         "distance_km", "elevation_m", "ftp", "np", "avg_power", "if", "tss",
                                         "tss_source", "kj", "avg_hr", "max_hr", "ef", "decoupling_pct",
                                         "coasting_s", "low_res", "indoor")},
        "efforts": efforts, "sets": sets, "w_prime_balance": wbal,
        "power_zone_s": row["power_zone_s"], "hr_zone_s": row["hr_zone_s"],
        "plan": plan, "compliance": compliance(plan, efforts) if plan else [],
    }


def render(res):
    a = res["activity"]
    lines = [f"## {a['date']} {a['name']} ({a['sport_type']}, {a['category']}"
             + (f" + {', '.join(a['secondary'])}" if a["secondary"] else "") + ")",
             f"{m.fmt_dur(a['moving_s'])} moving, {a['distance_km']} km, {a['elevation_m']} m. "
             f"NP {a['np']} W, avg {a['avg_power']} W, IF {a['if']}, TSS {a['tss']} ({a['tss_source']}), "
             f"{a['kj']} kJ. HR avg {a['avg_hr']} / max {a['max_hr']}, EF {a['ef']}, "
             f"decoupling {a['decoupling_pct'] if a['decoupling_pct'] is not None else 'n/a (not steady)'}"
             f"{'%' if a['decoupling_pct'] is not None else ''}. Coasting {m.fmt_dur(a['coasting_s'] or 0)}."]
    if a["low_res"]:
        lines.append("_Coarse recording (4-8 s samples): NP/TSS taken from the head unit; "
                     "peaks under 5 min are not reliable._")
    if res["efforts"]:
        lines += ["", "### Work intervals (" + res["efforts"][0]["source"] + ")", table(
            ["#", "Start", "Duration", "Avg W", "% FTP", "Avg HR", "Max HR", "Cadence", "HR drop 60s", "Type", "Set"],
            [[i + 1, m.fmt_dur(e["start"]) if e["start"] is not None else "", m.fmt_dur(e["duration"]),
              e["avg_power"], f"{e['pct_ftp']:.0%}", e["avg_hr"], e["max_hr"], e["avg_cadence"],
              e.get("hr_recovery_60s"), e["category"], e.get("set")]
             for i, e in enumerate(res["efforts"])])]
    if res["sets"]:
        lines += ["", "### Sets", table(
            ["Set", "Reps", "Avg duration", "Avg W", "% FTP", "Type", "Fade first->last", "Power CV", "HR drift",
             "W/beat change"],
            [[i + 1, s["reps"], m.fmt_dur(s["avg_duration"]), s["avg_power"], f"{s['pct_ftp']:.0%}", s["category"],
              f"{s['fade_pct']:+}%", f"{s['power_cv_pct']}%",
              f"{s['hr_drift_bpm']:+} bpm" if s["hr_drift_bpm"] is not None else "",
              f"{s['watts_per_beat_change_pct']:+}%" if s["watts_per_beat_change_pct"] is not None else ""]
             for i, s in enumerate(res["sets"])])]
    if w := res["w_prime_balance"]:
        lines += ["", f"W' balance (CP {w['cp']} W, W' {w['w_prime_j']} J from prior 90 days): lowest "
                      f"{w['min_pct']:.0%} at {m.fmt_dur(w['at_s'])}"
                  + (" - fully emptied, CP/W' likely underestimated" if w["min_pct"] < 0 else "")]
    if res["compliance"]:
        lines += ["", "### Plan vs done"]
        for c in res["compliance"]:
            target = f" @ {c['target_w']} W" if c["target_w"] else ""
            if c["verdict"] == "not found":
                lines.append(f"- {c['reps']}x{m.fmt_dur(c['duration'])}{target}: not found in the ride")
            else:
                vs = f" ({c['power_vs_target_pct']:+}% vs target)" if c["power_vs_target_pct"] is not None else ""
                short = (f", {len(c['short_reps'])} cut short ({', '.join(map(m.fmt_dur, c['short_reps']))})"
                         if c["short_reps"] else "")
                lines.append(f"- {c['reps']}x{m.fmt_dur(c['duration'])}{target}: did {c['done_reps']} reps "
                             f"({c['full_reps']} full{short}) @ {c['done_power']} W{vs} -> **{c['verdict']}**")
    if res["power_zone_s"]:
        tot = sum(res["power_zone_s"]) or 1
        lines += ["", "Time in power zones: " + ", ".join(
            f"Z{i + 1} {s / tot:.0%}" for i, s in enumerate(res["power_zone_s"]))]
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    p.add_argument("id", nargs="?", default="latest")
    p.add_argument("--date")
    p.add_argument("--plan")
    args = p.parse_args()
    rows = derive.ensure()
    row = select(rows, args.id, args.date)
    emit(analyse(rows, row, args.plan), args.json, render)


if __name__ == "__main__":
    main()
