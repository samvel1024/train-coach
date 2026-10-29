#!/usr/bin/env python3
"""Backfill Strava training data into local JSONL files.

Reuses the OAuth credentials of the strava MCP server (~/.config/strava-mcp/config.json)
and writes refreshed tokens back there so both stay in sync.

Two phases:
  fetch  - downloads detailed activity (incl. laps, splits, best efforts, segment efforts)
           and full-resolution streams for every activity in the window, one gzipped raw
           file per activity in <out>/raw/. Already-downloaded activities are skipped, so the
           job is resumable. Strava rate limits are respected by sleeping until the window resets.
  build  - compiles the raw files into:
             <out>/activities.jsonl  one row per activity (detailed activity, laps removed)
             <out>/laps.jsonl        one row per lap
             <out>/streams.jsonl     one row per activity: time, hr, power, cadence, gps, ... arrays
             <out>/athlete.json      athlete profile + HR/power zones

Usage:
  python3 backfill.py                 # last 365 days, fetch + build
  python3 backfill.py --days 30
  python3 backfill.py --build-only
"""
import argparse
import datetime as dt
import gzip
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
CONFIG = Path.home() / ".config/strava-mcp/config.json"
STREAM_KEYS = [
    "time", "distance", "latlng", "altitude", "velocity_smooth", "heartrate",
    "cadence", "watts", "temp", "moving", "grade_smooth",
]


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def write_atomic(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class Strava:
    def __init__(self):
        self.cfg = json.loads(CONFIG.read_text())

    def token(self):
        if self.cfg["expiresAt"] - time.time() < 300:
            self.refresh()
        return self.cfg["accessToken"]

    def refresh(self):
        # The MCP server may have refreshed in the meantime.
        cfg = json.loads(CONFIG.read_text())
        if cfg["expiresAt"] - time.time() >= 300 and cfg["accessToken"] != self.cfg["accessToken"]:
            self.cfg = cfg
            return
        body = urllib.parse.urlencode({
            "client_id": cfg["clientId"],
            "client_secret": cfg["clientSecret"],
            "grant_type": "refresh_token",
            "refresh_token": cfg["refreshToken"],
        }).encode()
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=30) as r:
            tok = json.load(r)
        cfg.update(accessToken=tok["access_token"], refreshToken=tok["refresh_token"],
                   expiresAt=tok["expires_at"])
        mode = CONFIG.stat().st_mode & 0o777
        write_atomic(CONFIG, json.dumps(cfg, indent=2).encode())
        os.chmod(CONFIG, mode)
        self.cfg = cfg
        log("refreshed access token")

    def get(self, path, **params):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(10):
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token()}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.load(r)
                    self.throttle(r.headers)
                    return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 401 and attempt == 0:
                    self.cfg["expiresAt"] = 0
                    continue
                if e.code == 429:
                    log("429 rate limited")
                    self.sleep_until_window(e.headers)
                    continue
                if e.code >= 500:
                    time.sleep(30 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
                log(f"network error {e}, retrying")
                time.sleep(30 * (attempt + 1))
        raise RuntimeError(f"giving up on {path}")

    @staticmethod
    def _limits(headers):
        # Newer apps get separate read limits; fall back to the overall ones.
        for prefix in ("X-ReadRateLimit", "X-RateLimit"):
            limit, usage = headers.get(f"{prefix}-Limit"), headers.get(f"{prefix}-Usage")
            if limit and usage:
                (l15, lday), (u15, uday) = (map(int, limit.split(",")), map(int, usage.split(",")))
                return l15, lday, u15, uday
        return None

    def throttle(self, headers):
        lim = self._limits(headers)
        if lim and (lim[2] >= lim[0] - 1 or lim[3] >= lim[1] - 1):
            self.sleep_until_window(headers)

    def sleep_until_window(self, headers):
        now = time.time()
        lim = self._limits(headers)
        if lim and lim[3] >= lim[1] - 1:
            tomorrow = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
            until = dt.datetime.combine(tomorrow, dt.time(), dt.timezone.utc).timestamp() + 60
            reason = "daily"
        else:
            until = (now // 900 + 1) * 900 + 5  # 15-min windows reset at :00/:15/:30/:45
            reason = "15-min"
        log(f"{reason} rate limit reached, sleeping {int(until - now)}s "
            f"until {dt.datetime.fromtimestamp(until):%H:%M}")
        time.sleep(until - now)


def list_activities(api, after):
    out, page = [], 1
    while True:
        batch = api.get("/athlete/activities", after=int(after), per_page=200, page=page)
        if not batch:
            return out
        out.extend(batch)
        page += 1


def fetch(api, out: Path, days: int, limit: int | None):
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    athlete = {"profile": api.get("/athlete"), "zones": api.get("/athlete/zones")}
    write_atomic(out / "athlete.json", json.dumps(athlete, indent=2).encode())

    after = time.time() - days * 86400
    summaries = sorted(list_activities(api, after), key=lambda a: a["start_date"])
    todo = [a for a in summaries if not (raw / f"{a['id']}.json.gz").exists()]
    if limit:
        todo = todo[:limit]
    log(f"{len(summaries)} activities in last {days} days, {len(todo)} to fetch")

    for i, s in enumerate(todo, 1):
        aid = s["id"]
        detail = api.get(f"/activities/{aid}", include_all_efforts="true")
        streams = api.get(f"/activities/{aid}/streams", keys=",".join(STREAM_KEYS), key_by_type="true")
        record = {"summary": s, "detail": detail, "streams": streams or {}}
        write_atomic(raw / f"{aid}.json.gz", gzip.compress(json.dumps(record).encode()))
        log(f"[{i}/{len(todo)}] {s['start_date'][:10]} {s['sport_type']:<14} {s['name']} ({aid})")


def build(out: Path):
    records = []
    for f in (out / "raw").glob("*.json.gz"):
        records.append(json.loads(gzip.decompress(f.read_bytes())))
    records.sort(key=lambda r: r["summary"]["start_date"])

    acts, laps, streams = [], [], []
    for r in records:
        act = dict(r["detail"] or r["summary"])
        aid = act["id"]
        for lap in act.pop("laps", None) or []:
            laps.append(json.dumps({"activity_id": aid, **lap}))
        acts.append(json.dumps(act))

        st = r["streams"]
        row = {
            "activity_id": aid,
            "start_date": act.get("start_date"),
            "sport_type": act.get("sport_type"),
            "meta": {k: {m: v.get(m) for m in ("series_type", "original_size", "resolution")}
                     for k, v in st.items()},
        }
        row.update({k: v["data"] for k, v in st.items()})
        streams.append(json.dumps(row))

    for name, rows in (("activities", acts), ("laps", laps), ("streams", streams)):
        write_atomic(out / f"{name}.jsonl", ("\n".join(rows) + "\n").encode() if rows else b"")
    log(f"built {len(acts)} activities, {len(laps)} laps, {len(streams)} stream rows in {out}/")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "data")
    p.add_argument("--limit", type=int, help="fetch at most N new activities (for testing)")
    p.add_argument("--build-only", action="store_true")
    args = p.parse_args()

    if not args.build_only:
        fetch(Strava(), args.out, args.days, args.limit)
    build(args.out)


if __name__ == "__main__":
    main()
