"""Athlete state: FTP (set vs estimated), zones, threshold HR, current load, last 7 days.

  python3 -m coach.athlete_state [--json]
"""
import datetime as dt

from . import data, derive, metrics as m, power_profile, training_load
from .report import emit, hours, parser, table


def lthr_estimate(rows, days=120):
    """Median HR during the best 20 min of rides where that 20 min was >= 90% FTP."""
    start = data.today() - dt.timedelta(days=days)
    hrs = sorted(r["best20_hr"] for r in rows
                 if r["best20_hr"] and data.parse_day(r["date"]) >= start
                 and r["mmp"].get("1200", 0) >= 0.9 * r["ftp"] and not r["low_res"])
    return hrs[len(hrs) // 2] if hrs else None


def ftp_advice(est):
    ftp, e20, cp = est["ftp_set"], est["ftp_from_20min"], est["cp"]
    best = max(x for x in (e20, cp, 0) if x is not None)
    if best > ftp * 1.03:
        return f"Recent efforts suggest FTP ~{best} W, above the set {ftp} W: consider raising it."
    if best and best < ftp * 0.95:
        return (f"No maximal effort in the last {est['window_days']} days supports {ftp} W "
                f"(best estimate {best} W). Either test, or treat FTP as unverified.")
    return f"Set FTP {ftp} W is consistent with recent efforts (estimates {e20} / {cp} W)."


def state(rows):
    today = data.today()
    ftp, kg = data.ftp_on(today.isoformat()), data.weight()
    est = power_profile.estimates(rows)
    last7 = [r for r in rows if data.parse_day(r["date"]) > today - dt.timedelta(days=7)]
    year = [r["max_hr"] for r in rows if r["max_hr"] and r["max_hr"] < 220
            and data.parse_day(r["date"]) > today - dt.timedelta(days=365)]
    return {
        "date": today.isoformat(), "weight_kg": kg, "ftp": ftp, "w_per_kg": round(ftp / kg, 2),
        "ftp_estimates": est, "ftp_advice": ftp_advice(est),
        "power_zones": [{"zone": name, "min_w": round(lo), "max_w": round(hi) if hi else None}
                        for name, lo, hi in zip(m.POWER_ZONE_NAMES, [0] + m.power_zone_limits(ftp),
                                                m.power_zone_limits(ftp) + [None])],
        "hr_zones_lower_bounds": data.hr_zone_bounds(), "lthr_config": data.lthr(),
        "lthr_estimate": lthr_estimate(rows), "max_hr_365d": max(year) if year else None,
        "load": training_load.state(rows),
        "last7": {"sessions": len(last7), "moving_s": sum(r["moving_s"] for r in last7),
                  "tss": round(sum(r["tss"] or 0 for r in last7)),
                  "types": [f"{r['date']} {r['category']}" for r in last7]},
    }


def render(s):
    e, ld, l7 = s["ftp_estimates"], s["load"], s["last7"]
    lines = [f"**FTP {s['ftp']} W** ({s['w_per_kg']} W/kg at {s['weight_kg']} kg). {s['ftp_advice']}",
             f"Estimates (last {e['window_days']}d): 95% of best 20 min = {e['ftp_from_20min']} W, "
             f"CP = {e['cp']} W, W' = {e['w_prime_j']} J, best 60 min = {e['best60']} W.",
             f"Threshold HR: {s['lthr_config']} bpm used for hrTSS; estimated from hard 20-min efforts "
             f"{s['lthr_estimate']} bpm. Max HR seen in the last year: {s['max_hr_365d']} bpm.",
             f"Load: CTL {ld['ctl']}, ATL {ld['atl']}, TSB {ld['tsb']} ({ld['form']}), ramp {ld['ramp_7d']:+}/7d.",
             f"Last 7 days: {l7['sessions']} sessions, {hours(l7['moving_s'])}, {l7['tss']} TSS "
             f"({', '.join(l7['types'])}).", "",
             table(["Power zone", "Watts"],
                   [[z["zone"], f"{z['min_w']}-{z['max_w']}" if z["max_w"] else f"{z['min_w']}+"]
                    for z in s["power_zones"]]),
             "", "HR zone lower bounds (Z2..Z5): " + ", ".join(map(str, s["hr_zones_lower_bounds"]))]
    return "\n".join(lines)


def main():
    args = parser(__doc__).parse_args()
    emit(state(derive.ensure()), args.json, render)


if __name__ == "__main__":
    main()
