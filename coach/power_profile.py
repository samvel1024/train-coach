"""Power profile: best power per duration, monthly trend, CP/W', FTP estimates, PRs, durability.

  python3 -m coach.power_profile [--pr-days 28] [--json]

Peaks shorter than 5 min are skipped on coarse (4-8 s) recordings, where they are inflated.
"""
import datetime as dt
from collections import defaultdict

from . import data, derive, metrics as m
from .report import emit, parser, table

KEY_DURATIONS = ("5", "60", "300", "1200", "3600")
LABELS = {"5": "5s", "15": "15s", "30": "30s", "60": "1min", "120": "2min", "300": "5min", "600": "10min",
          "1200": "20min", "1800": "30min", "3600": "60min", "5400": "90min", "7200": "2h"}


def bests(rows, start=None, end=None):
    """{duration: {watts, date, id, name}} over cycling rows in [start, end]."""
    out = {}
    for r in rows:
        day = data.parse_day(r["date"])
        if (start and day < start) or (end and day > end) or not r["has_power"]:
            continue
        for d, w in r["mmp"].items():
            if d not in out or w > out[d]["watts"]:
                out[d] = {"watts": w, "date": r["date"], "id": r["id"], "name": r["name"]}
    return out


def estimates(rows, on=None, days=90):
    """FTP/CP estimates from the `days` before `on` (default today)."""
    on = on or data.today()
    b = bests(rows, on - dt.timedelta(days=days), on)
    mmp = {int(d): v["watts"] for d, v in b.items()}
    cp, w_prime = m.cp_fit(mmp, (300, 600, 1200))
    best20, best60 = mmp.get(1200), mmp.get(3600)
    best20, best60 = best20 and round(best20), best60 and round(best60)
    return {
        "window_days": days, "ftp_set": data.ftp_on(on.isoformat()),
        "best20": best20, "ftp_from_20min": round(best20 * 0.95) if best20 else None,
        "best60": best60, "cp": round(cp) if cp else None, "w_prime_j": round(w_prime) if w_prime else None,
    }


def monthly(rows, durations=KEY_DURATIONS):
    by_month = defaultdict(dict)
    for r in rows:
        if not r["has_power"]:
            continue
        mon = r["date"][:7]
        for d in durations:
            w = r["mmp"].get(d)
            if w and w > by_month[mon].get(d, 0):
                by_month[mon][d] = w
    return dict(sorted(by_month.items()))


def prs(rows, pr_days=28, history_days=365):
    """Durations where the last `pr_days` beat the best of the preceding `history_days`."""
    today = data.today()
    cut = today - dt.timedelta(days=pr_days)
    recent = bests(rows, cut + dt.timedelta(days=1), today)
    before = bests(rows, cut - dt.timedelta(days=history_days), cut)
    out = []
    for d, v in recent.items():
        prev = before.get(d)
        if prev is None or v["watts"] > prev["watts"]:
            out.append({"duration": d, **v, "previous": prev and prev["watts"]})
    return sorted(out, key=lambda x: int(x["duration"]))


def durability(rows, days=90):
    """Best 5/20-min power fresh vs after 1000/2000/3000 kJ."""
    start = data.today() - dt.timedelta(days=days)
    fresh = bests(rows, start)
    out = {}
    for r in rows:
        if data.parse_day(r["date"]) < start:
            continue
        for kj, vals in r["mmp_after_kj"].items():
            for d, w in vals.items():
                if w > out.get(kj, {}).get(d, 0):
                    out.setdefault(kj, {})[d] = w
    res = []
    for kj in sorted(out, key=int):
        row = {"after_kj": int(kj)}
        for d in ("300", "1200"):
            w, f = out[kj].get(d), fresh.get(d, {}).get("watts")
            row[d] = w
            row[f"{d}_retained"] = round(w / f, 3) if w and f else None
        res.append(row)
    return res


def profile(rows, pr_days=28):
    today = data.today()
    kg = data.weight()
    windows = {}
    for label, days in (("42d", 42), ("90d", 90), ("365d", 365)):
        windows[label] = bests(rows, today - dt.timedelta(days=days))
    return {"weight_kg": kg, "windows": windows, "estimates": estimates(rows),
            "monthly": monthly(rows), "prs": prs(rows, pr_days), "durability": durability(rows)}


def render(p):
    kg = p["weight_kg"]
    durs = sorted(p["windows"]["365d"], key=int)
    lines = ["## Best power", table(
        ["Duration"] + [f"{w} W (W/kg)" for w in p["windows"]] + ["365d set on"],
        [[LABELS.get(d, d)] + [f"{v['watts']:.0f} ({v['watts'] / kg:.2f})" if (v := p["windows"][w].get(d)) else ""
                               for w in p["windows"]] + [p["windows"]["365d"][d]["date"]] for d in durs])]
    e = p["estimates"]
    lines += ["", f"## FTP estimates (last {e['window_days']} days)",
              f"- FTP set: **{e['ftp_set']} W** ({e['ftp_set'] / kg:.2f} W/kg)",
              f"- 95% of best 20 min ({e['best20']} W): **{e['ftp_from_20min']} W**",
              f"- Critical power (5/10/20 min fit): **{e['cp']} W**, W' {e['w_prime_j']} J",
              f"- Best 60 min: {e['best60']} W",
              "- These are lower bounds: they only reflect maximal efforts you actually did."]
    lines += ["", "## Monthly bests", table(
        ["Month"] + [LABELS[d] for d in KEY_DURATIONS],
        [[mon] + [f"{v[d]:.0f}" if d in v else "" for d in KEY_DURATIONS] for mon, v in p["monthly"].items()])]
    lines += ["", "## New bests in the last 28 days (vs prior 365 days)"]
    lines += [f"- {LABELS.get(x['duration'], x['duration'])}: {x['watts']:.0f} W on {x['date']} ({x['name']})"
              + (f", previous {x['previous']:.0f} W" if x["previous"] else "") for x in p["prs"]] or ["- none"]
    lines += ["", "## Durability (last 90 days): best power after accumulated work", table(
        ["After", "5min W", "% of fresh", "20min W", "% of fresh"],
        [[f"{d['after_kj']} kJ", d["300"], f"{d['300_retained']:.0%}" if d["300_retained"] else "",
          d["1200"], f"{d['1200_retained']:.0%}" if d["1200_retained"] else ""] for d in p["durability"]])]
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    p.add_argument("--pr-days", type=int, default=28)
    args = p.parse_args()
    emit(profile(derive.ensure(), args.pr_days), args.json, render)


if __name__ == "__main__":
    main()
