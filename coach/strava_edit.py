"""Edit a Strava activity's sport type / bike / name (only when the user asks for it).

Needs the activity:write scope on the strava-mcp OAuth token (~/.config/strava-mcp/config.json).

  python3 -m coach.strava_edit ID [--sport-type MountainBikeRide] [--gear GEAR_ID] [--name ..]
  python3 -m coach.strava_edit --gear-list
  python3 -m coach.strava_edit --auth-url              URL to grant activity:write (user opens it)
  python3 -m coach.strava_edit --auth-code CODE        exchange the code from the redirect URL
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from backfill import API, CONFIG, TOKEN_URL, Strava, write_atomic

SCOPE = "read,read_all,profile:read_all,activity:read_all,activity:write"


def auth_url(cfg):
    return "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": cfg["clientId"], "response_type": "code", "redirect_uri": "http://localhost",
        "approval_prompt": "force", "scope": SCOPE})


def exchange(cfg, code):
    body = urllib.parse.urlencode({"client_id": cfg["clientId"], "client_secret": cfg["clientSecret"],
                                   "code": code, "grant_type": "authorization_code"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"token exchange -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    cfg.update(accessToken=tok["access_token"], refreshToken=tok["refresh_token"], expiresAt=tok["expires_at"])
    mode = CONFIG.stat().st_mode & 0o777
    write_atomic(CONFIG, json.dumps(cfg, indent=2).encode())
    os.chmod(CONFIG, mode)
    print(f"token saved, expires in {int((tok['expires_at'] - time.time()) / 60)} min")


def put(client, path, body):
    req = urllib.request.Request(API + path, method="PUT", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {client.token()}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"PUT {path} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("id", nargs="?")
    p.add_argument("--sport-type")
    p.add_argument("--gear")
    p.add_argument("--name")
    p.add_argument("--gear-list", action="store_true")
    p.add_argument("--auth-url", action="store_true")
    p.add_argument("--auth-code")
    a = p.parse_args()
    client = Strava()
    if a.auth_url:
        print(auth_url(client.cfg))
        return
    if a.auth_code:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(a.auth_code).query).get("code", [a.auth_code])[0]
        exchange(client.cfg, code)
        return
    if a.gear_list:
        for b in client.get("/athlete").get("bikes", []):
            print(f"{b['id']:>12} {round(b.get('distance', 0) / 1000):>7} km  {b['name']}")
        return
    body = {k: v for k, v in (("sport_type", a.sport_type), ("gear_id", a.gear), ("name", a.name)) if v}
    if not a.id or not body:
        sys.exit("need ID and at least one of --sport-type/--gear/--name")
    act = put(client, f"/activities/{a.id}", body)
    print(f"{act['id']} {act['start_date_local'][:10]} {act['sport_type']} "
          f"gear={(act.get('gear') or {}).get('name')} {act['name']}")


if __name__ == "__main__":
    main()
