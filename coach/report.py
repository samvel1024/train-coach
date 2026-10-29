"""Shared CLI/report helpers."""
import argparse
import datetime as dt
import json

from . import data


def parser(doc):
    p = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def emit(obj, as_json, render):
    print(json.dumps(obj, indent=2, default=str) if as_json else render(obj))


def table(headers, rows):
    rows = [["" if c is None else str(c) for c in r] for r in rows]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def hours(seconds):
    return f"{seconds / 3600:.1f}h"


def pct(x, digits=0):
    return "" if x is None else f"{x * 100:.{digits}f}%"


def rows_between(rows, start: dt.date, end: dt.date):
    """Rows with start <= date <= end."""
    return [r for r in rows if start <= data.parse_day(r["date"]) <= end]


def since_arg(p, default_days):
    p.add_argument("--days", type=int, default=default_days, help=f"look-back window (default {default_days})")
    p.add_argument("--since", help="YYYY-MM-DD; overrides --days")


def since_date(args):
    return data.parse_day(args.since) if args.since else data.today() - dt.timedelta(days=args.days)
