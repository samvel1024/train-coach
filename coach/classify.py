"""Classify sessions: endurance, tempo, sweetspot, threshold, vo2max, anaerobic, race, ...

  python3 -m coach.classify [--days 35 | --since YYYY-MM-DD] [--category threshold] [--json]

Work intervals come from manual laps when present, otherwise they are detected in the
power stream (sets of repeated efforts only; random surges and climbs are ignored).
"""
from collections import Counter

from . import data, derive, metrics as m
from .report import emit, parser, since_arg, since_date, table


def sets_text(r):
    return m.summarize_efforts([e for e in r["efforts"] if e.get("set") is not None])


def sessions(rows, start, category=None):
    out = []
    for r in rows:
        if data.parse_day(r["date"]) < start:
            continue
        if category and category != r["category"] and category not in r["secondary"]:
            continue
        out.append({
            "id": r["id"], "date": r["date"], "name": r["name"], "sport_type": r["sport_type"],
            "category": r["category"], "secondary": r["secondary"], "moving_s": r["moving_s"],
            "if": r["if"], "tss": r["tss"], "work": sets_text(r),
            "work_source": r["efforts"][0]["source"] if r["structured"] and r["efforts"] else None,
        })
    return out


def render(res):
    lines = [table(
        ["Date", "Session", "Type", "Time", "IF", "TSS", "Work intervals"],
        [[s["date"], (s["name"] or "")[:28], s["category"] + (f" (+{', '.join(s['secondary'])})" if s["secondary"] else ""),
          m.fmt_dur(s["moving_s"]), s["if"], round(s["tss"] or 0), s["work"]
          + (f" [{s['work_source']}]" if s["work_source"] else "")] for s in res["sessions"]])]
    lines += ["", "**Count**: " + ", ".join(f"{k} {v}" for k, v in res["counts"].items())]
    return "\n".join(lines)


def main():
    p = parser(__doc__)
    since_arg(p, 35)
    p.add_argument("--category")
    args = p.parse_args()
    s = sessions(derive.ensure(), since_date(args), args.category)
    emit({"sessions": s, "counts": dict(Counter(x["category"] for x in s).most_common())}, args.json, render)


if __name__ == "__main__":
    main()
