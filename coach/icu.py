"""intervals.icu client + CLI for the coaching workflow.

Reads INTERVALS_API_KEY / INTERVALS_ATHLETE_ID from the environment, falling back to
noop_bridge/.env. Never prints the key.

  python3 -m coach.icu readiness [--date D]           today's WHOOP numbers vs 7-day baseline
  python3 -m coach.icu wellness [--days 10]           wellness table
  python3 -m coach.icu events [--from D] [--to D]     calendar (default: today .. +10 days)
  python3 -m coach.icu get ID                         one event as JSON
  python3 -m coach.icu create DATE --name N (--desc ..|--desc-file F) [--load N] [--minutes N]   planned ride
  python3 -m coach.icu update ID [--name ..] [--desc ..|--desc-file F] [--load N] [--minutes N] [--date D] [--end D]
  python3 -m coach.icu delete ID [ID ...]
  python3 -m coach.icu note DATE --title T (--body ..|--body-file F) [--week]
  python3 -m coach.icu gear                           bikes/shoes with ids
  python3 -m coach.icu activity ID [--type T] [--gear G] [--name ..]   show or update one activity
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / "noop_bridge" / ".env"
API = "https://intervals.icu/api/v1"
BASE = API + "/athlete/{}"


def _env():
    env = {k: os.environ.get(k) for k in ("INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID")}
    if not all(env.values()) and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() in env and not env[k.strip()]:
                env[k.strip()] = v.strip().strip("'\"")
    if not all(env.values()):
        sys.exit("INTERVALS_API_KEY / INTERVALS_ATHLETE_ID not set")
    return env


def call(method, path, body=None, athlete=True):
    env = _env()
    token = base64.b64encode(f"API_KEY:{env['INTERVALS_API_KEY']}".encode()).decode()
    req = urllib.request.Request(
        (BASE.format(env["INTERVALS_ATHLETE_ID"]) if athlete else API) + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Basic " + token, "User-Agent": "noop-bridge/1.0",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    return json.loads(raw) if raw else None


def wellness(oldest, newest):
    return call("GET", f"/wellness?oldest={oldest}&newest={newest}")


def events(oldest, newest):
    return call("GET", f"/events?oldest={oldest}&newest={newest}")


def today():
    return dt.date.today()


def _r(x, n=1):
    return None if x is None else round(x, n)


def cmd_wellness(a):
    rows = wellness(today() - dt.timedelta(days=a.days - 1), today())
    print(f"{'date':10} {'RHR':>4} {'HRV':>5} {'sleep':>5} {'rec':>4} {'resp':>5} {'CTL':>6} {'ATL':>6} {'form':>6} {'kg':>5}")
    for w in rows:
        ctl, atl = w.get("ctl"), w.get("atl")
        form = None if ctl is None or atl is None else ctl - atl
        sleep = _r((w.get("sleepSecs") or 0) / 3600) if w.get("sleepSecs") else None
        print(f"{w['id']:10} {str(w.get('restingHR') or '-'):>4} {str(_r(w.get('hrv'), 0) or '-'):>5} "
              f"{str(sleep or '-'):>5} {str(w.get('readiness') or '-'):>4} {str(_r(w.get('respiration')) or '-'):>5} "
              f"{str(_r(ctl)):>6} {str(_r(atl)):>6} {str(_r(form)):>6} {str(w.get('weight') or '-'):>5}")


def cmd_readiness(a):
    day = dt.date.fromisoformat(a.date) if a.date else today()
    rows = {w["id"]: w for w in wellness(day - dt.timedelta(days=7), day)}
    cur = rows.get(day.isoformat())
    if not cur or cur.get("restingHR") is None:
        sys.exit(f"no WHOOP data for {day} yet (NOOP push missing?)")
    base = [w for d, w in rows.items() if d != day.isoformat()]

    def mean(k):
        v = [w[k] for w in base if w.get(k) is not None]
        return sum(v) / len(v) if v else None

    rhr, hrv = cur.get("restingHR"), cur.get("hrv")
    rhr_b, hrv_b = mean("restingHR"), mean("hrv")
    print(f"{day}  RHR {rhr} (7d {_r(rhr_b)})  HRV {_r(hrv, 0)} (7d {_r(hrv_b, 0)})  "
          f"sleep {_r((cur.get('sleepSecs') or 0) / 3600)} h  recovery {cur.get('readiness')}  "
          f"resp {cur.get('respiration')} (7d {_r(mean('respiration'))})  "
          f"weight {cur.get('weight') or '-'} kg")
    flags = []
    if rhr_b and rhr - rhr_b >= 7:
        flags.append(f"RHR +{rhr - rhr_b:.0f} bpm")
    if hrv_b and hrv and hrv < 0.8 * hrv_b:
        flags.append(f"HRV {100 * (1 - hrv / hrv_b):.0f}% below baseline")
    r_b = mean("respiration")
    if r_b and cur.get("respiration") and cur["respiration"] - r_b >= 1.0:
        flags.append("respiration up >=1/min")
    print("FLAGS: " + "; ".join(flags) if flags else "no physiological red flags")


def cmd_events(a):
    oldest = a.from_ or today().isoformat()
    newest = a.to or (dt.date.fromisoformat(oldest) + dt.timedelta(days=10)).isoformat()
    for e in events(oldest, newest):
        wk = " [week]" if e.get("for_week") else ""
        print(f"{e['id']:>10} {e['start_date_local'][:10]} {e['category']:8} "
              f"{str(e.get('icu_training_load') or ''):>4}  {e.get('name')}{wk}")


def cmd_get(a):
    print(json.dumps(call("GET", f"/events/{a.id}"), indent=2, ensure_ascii=False))


def _text(inline, path):
    return Path(path).read_text() if path else inline


def cmd_update(a):
    body = {}
    if a.name:
        body["name"] = a.name
    desc = _text(a.desc, a.desc_file)
    if desc is not None:
        body["description"] = desc
    if a.load is not None:
        body["icu_training_load"] = a.load
    if a.minutes is not None:
        body["moving_time"] = a.minutes * 60
    if a.date:
        body["start_date_local"] = f"{a.date}T00:00:00"
    if a.end:
        body["end_date_local"] = f"{a.end}T00:00:00"
    if not body:
        sys.exit("nothing to update")
    e = call("PUT", f"/events/{a.id}", body)
    print(f"updated {e['id']} {e['start_date_local'][:10]} {e.get('name')} load={e.get('icu_training_load')}")


def cmd_create(a):
    body = {"category": "WORKOUT", "type": "Ride", "name": a.name, "description": _text(a.desc, a.desc_file),
            "start_date_local": f"{dt.date.fromisoformat(a.date)}T00:00:00"}
    if a.load is not None:
        body["icu_training_load"] = a.load
    if a.minutes is not None:
        body["moving_time"] = a.minutes * 60
    e = call("POST", "/events", body)
    print(f"created {e['id']} {e['start_date_local'][:10]} {e.get('name')} load={e.get('icu_training_load')}")


def cmd_delete(a):
    for i in a.ids:
        call("DELETE", f"/events/{i}")
        print(f"deleted {i}")


def cmd_note(a):
    start = dt.date.fromisoformat(a.date)
    body = {"category": "NOTE", "name": a.title, "description": _text(a.body, a.body_file),
            "start_date_local": f"{start}T00:00:00"}
    if a.week:
        monday = start - dt.timedelta(days=start.weekday())
        body.update(for_week=True, start_date_local=f"{monday}T00:00:00",
                    end_date_local=f"{monday + dt.timedelta(days=7)}T00:00:00")
    e = call("POST", "/events", body)
    print(f"created NOTE {e['id']} {e['start_date_local'][:10]} {e.get('name')}")


def cmd_gear(a):
    for g in call("GET", "/gear"):
        if not g.get("retired"):
            print(f"{g['id']:>12} {g.get('type') or '':10} {round((g.get('distance') or 0) / 1000):>7} km  {g.get('name')}")


def cmd_activity(a):
    body = {k: v for k, v in (("type", a.type), ("gear", a.gear and {"id": a.gear}), ("name", a.name)) if v}
    act = call("PUT" if body else "GET", f"/activity/{a.id}", body or None, athlete=False)
    gear = (act.get("gear") or {}).get("name") or (act.get("gear") or {}).get("id")
    print(f"{act['id']} {(act.get('start_date_local') or '')[:10]} {act.get('type')} gear={gear} {act.get('name')}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="coach.icu")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("readiness"); s.add_argument("--date"); s.set_defaults(f=cmd_readiness)
    s = sub.add_parser("wellness"); s.add_argument("--days", type=int, default=10); s.set_defaults(f=cmd_wellness)
    s = sub.add_parser("events"); s.add_argument("--from", dest="from_"); s.add_argument("--to"); s.set_defaults(f=cmd_events)
    s = sub.add_parser("get"); s.add_argument("id"); s.set_defaults(f=cmd_get)
    s = sub.add_parser("update"); s.add_argument("id")
    for o in ("--name", "--desc", "--desc-file", "--date", "--end"):
        s.add_argument(o)
    s.add_argument("--load", type=int); s.add_argument("--minutes", type=int); s.set_defaults(f=cmd_update)
    s = sub.add_parser("create"); s.add_argument("date"); s.add_argument("--name", required=True)
    s.add_argument("--desc"); s.add_argument("--desc-file")
    s.add_argument("--load", type=int); s.add_argument("--minutes", type=int); s.set_defaults(f=cmd_create)
    s = sub.add_parser("delete"); s.add_argument("ids", nargs="+"); s.set_defaults(f=cmd_delete)
    s = sub.add_parser("note"); s.add_argument("date"); s.add_argument("--title", required=True)
    s.add_argument("--body"); s.add_argument("--body-file"); s.add_argument("--week", action="store_true")
    s.set_defaults(f=cmd_note)
    s = sub.add_parser("gear"); s.set_defaults(f=cmd_gear)
    s = sub.add_parser("activity"); s.add_argument("id")
    for o in ("--type", "--gear", "--name"):
        s.add_argument(o)
    s.set_defaults(f=cmd_activity)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
