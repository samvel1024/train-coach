#!/usr/bin/env python3
"""NOOP push receiver -> intervals.icu wellness.

Implements the receiving side of NOOP's self-hosted push protocol v1.0
(https://github.com/ryanbr/noop/blob/main/docs/PUSH_PROTOCOL.md) for the `dailyMetric`
stream only, stores the days durably in SQLite, and forwards resting HR, HRV, sleep,
readiness, SpO2, respiration and steps to intervals.icu wellness.

  python3 noop_bridge/receiver.py              # serve on 0.0.0.0:8787
  python3 noop_bridge/receiver.py --port 9000
  python3 noop_bridge/receiver.py --forward    # push pending days to intervals.icu and exit

Configuration (noop_bridge/.env, chmod 600):
  NOOP_TOKEN=<bearer token you also enter in NOOP>
  INTERVALS_API_KEY=<intervals.icu API key>
  INTERVALS_ATHLETE_ID=<e.g. i123456>

In NOOP (Android): Settings -> Experimental -> push endpoint
  http://<this Mac's LAN IP>:8787/noop   with the NOOP_TOKEN as bearer token.
NOOP only allows plain HTTP to numeric private IPs, so use the IP, not a hostname.
"""
import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import threading
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "cloud"))
from noop_protocol import (MAX_WIRE, ProtocolError, ack_for, authorized, capabilities_doc,  # noqa: E402
                           decode_body, error_doc, parse_batch, put_wellness, to_wellness)

FORWARD_EVERY_S = 600

log = logging.getLogger("noop_bridge")


def load_env(path=HERE / ".env"):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# ------------------------------------------------------------------ storage

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batch (source_id TEXT, device_id TEXT, batch_id TEXT, sha256 TEXT NOT NULL,
                ack TEXT NOT NULL, PRIMARY KEY (source_id, device_id, batch_id));
            CREATE TABLE IF NOT EXISTS staging (source_id TEXT, device_id TEXT, stream TEXT, replacement_id TEXT,
                part INTEGER, parts INTEGER, window TEXT NOT NULL, records TEXT NOT NULL,
                PRIMARY KEY (source_id, device_id, stream, replacement_id, part));
            CREATE TABLE IF NOT EXISTS generation (source_id TEXT, device_id TEXT, stream TEXT, replacement_id TEXT,
                state TEXT NOT NULL, PRIMARY KEY (source_id, device_id, stream, replacement_id));
            CREATE TABLE IF NOT EXISTS daily (source_id TEXT, device_id TEXT, day TEXT, data TEXT NOT NULL,
                forwarded INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (source_id, device_id, day));
        """)
        if not self.db.execute("SELECT v FROM meta WHERE k='receiverStateId'").fetchone():
            self.db.execute("INSERT INTO meta VALUES ('receiverStateId', ?)", (str(uuid.uuid4()),))

    @property
    def receiver_state_id(self):
        return self.db.execute("SELECT v FROM meta WHERE k='receiverStateId'").fetchone()[0]


# ------------------------------------------------------------------ protocol

def capabilities(store, accept_header):
    return capabilities_doc(store.receiver_state_id, accept_header)


def accept_batch(store, entity: bytes):
    """Validate, stage and (when complete) apply one replace-window part. Returns the ack dict."""
    header, records = parse_batch(entity)
    digest = hashlib.sha256(entity).hexdigest()
    src, dev, stream, w = header["sourceId"], header["deviceId"], header["stream"], header["window"]
    with store.lock:
        db = store.db
        prev = db.execute("SELECT sha256, ack FROM batch WHERE source_id=? AND device_id=? AND batch_id=?",
                          (src, dev, header["batchId"])).fetchone()
        if prev:
            if prev[0] != digest:
                raise ProtocolError(409, "batch_conflict")
            return json.loads(prev[1])
        rid = w["replacementId"]
        gen = db.execute("SELECT state FROM generation WHERE source_id=? AND device_id=? AND stream=? "
                         "AND replacement_id=?", (src, dev, stream, rid)).fetchone()
        if gen and gen[0] != "open":
            raise ProtocolError(409, "superseded_generation" if gen[0] == "superseded" else "generation_applied")
        db.execute("BEGIN IMMEDIATE")
        try:
            if not gen:
                # A new generation supersedes every older incomplete one in this scope.
                db.execute("UPDATE generation SET state='superseded' WHERE source_id=? AND device_id=? "
                           "AND stream=? AND state='open'", (src, dev, stream))
                db.execute("DELETE FROM staging WHERE source_id=? AND device_id=? AND stream=?", (src, dev, stream))
                db.execute("INSERT INTO generation VALUES (?,?,?,?, 'open')", (src, dev, stream, rid))
            win = json.dumps({k: w[k] for k in ("selector", "startInclusive", "endExclusive", "parts")})
            other = db.execute("SELECT window FROM staging WHERE source_id=? AND device_id=? AND stream=? "
                               "AND replacement_id=? LIMIT 1", (src, dev, stream, rid)).fetchone()
            if other and other[0] != win:
                raise ProtocolError(409, "window_conflict")
            if db.execute("SELECT 1 FROM staging WHERE source_id=? AND device_id=? AND stream=? AND "
                          "replacement_id=? AND part=?", (src, dev, stream, rid, w["part"])).fetchone():
                raise ProtocolError(409, "part_conflict")
            db.execute("INSERT INTO staging VALUES (?,?,?,?,?,?,?,?)",
                       (src, dev, stream, rid, w["part"], w["parts"], win, json.dumps(records)))
            staged = db.execute("SELECT records FROM staging WHERE source_id=? AND device_id=? AND stream=? "
                                "AND replacement_id=?", (src, dev, stream, rid)).fetchall()
            if len(staged) == w["parts"]:
                apply_window(db, src, dev, w, [r for (rs,) in staged for r in json.loads(rs)])
                db.execute("DELETE FROM staging WHERE source_id=? AND device_id=? AND stream=? AND "
                           "replacement_id=?", (src, dev, stream, rid))
                db.execute("UPDATE generation SET state='applied' WHERE source_id=? AND device_id=? AND "
                           "stream=? AND replacement_id=?", (src, dev, stream, rid))
            ack = ack_for(header)
            db.execute("INSERT INTO batch VALUES (?,?,?,?,?)", (src, dev, header["batchId"], digest, json.dumps(ack)))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return ack


def apply_window(db, src, dev, w, records):
    days = [r["day"] for r in records]
    if len(days) != len(set(days)):
        raise ProtocolError(400, "duplicate_key")
    for r in records:
        data = json.dumps(r["data"], sort_keys=True)
        old = db.execute("SELECT data FROM daily WHERE source_id=? AND device_id=? AND day=?",
                         (src, dev, r["day"])).fetchone()
        if not old or old[0] != data:
            db.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,0)", (src, dev, r["day"], data))
    # Absence means delete inside the window (locally; intervals.icu values are left in place).
    placeholders = ",".join("?" * len(days)) or "''"
    db.execute(f"DELETE FROM daily WHERE source_id=? AND device_id=? AND day>=? AND day<? "
               f"AND day NOT IN ({placeholders})", (src, dev, w["startInclusive"], w["endExclusive"], *days))


# ------------------------------------------------------------------ intervals.icu

def forward_pending(store, env, put=put_wellness):
    """Send changed days to intervals.icu; returns the number of days forwarded."""
    with store.lock:
        rows = store.db.execute("SELECT source_id, device_id, day, data FROM daily WHERE forwarded=0 "
                                "ORDER BY day").fetchall()
    sent = 0
    for src, dev, day, data in rows:
        wellness = to_wellness(day, json.loads(data))
        if len(wellness) > 1:
            try:
                put(env, wellness)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log.warning("forward %s failed (%s); will retry", day, e)
                return sent
        with store.lock:
            store.db.execute("UPDATE daily SET forwarded=1 WHERE source_id=? AND device_id=? AND day=? AND data=?",
                             (src, dev, day, data))
        sent += 1
        log.info("forwarded %s %s", day, wellness)
    return sent


# ------------------------------------------------------------------ HTTP

def make_handler(store, env, path="/noop", on_apply=None):
    token = env["NOOP_TOKEN"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "noop-bridge/1"

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status, obj):
            body = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status, code):
            self._send(status, error_doc(code))

        def _authorized(self):
            return authorized(self.headers.get("Authorization"), token)

        def _guard(self):
            if self.path.split("?")[0] != path:
                self._error(404, "not_found")
                return False
            if not self._authorized():
                self._error(401, "unauthorized")
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            try:
                self._send(200, capabilities(store, self.headers.get("NOOP-Push-Accept-Version")))
            except ProtocolError as e:
                self._error(e.status, e.code)

        def do_POST(self):
            if not self._guard():
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0:
                    raise ProtocolError(411, "length_required")
                if length > MAX_WIRE:
                    raise ProtocolError(413, "too_large")
                body = decode_body(self.rfile.read(length), self.headers.get("Content-Encoding"))
                ack = accept_batch(store, body)
            except ProtocolError as e:
                self._error(e.status, e.code)
                return
            except Exception:
                log.exception("receiver failure")
                self._error(500, "receiver_failure")
                return
            self._send(200, ack)
            if on_apply:
                on_apply()

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--db", default=str(HERE / "state.sqlite"))
    p.add_argument("--forward", action="store_true", help="forward pending days and exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = load_env()
    missing = [k for k in ("NOOP_TOKEN", "INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID") if not env.get(k)]
    if missing:
        raise SystemExit(f"missing in {HERE / '.env'}: {', '.join(missing)}")
    store = Store(args.db)
    if args.forward:
        print(f"forwarded {forward_pending(store, env)} day(s)")
        return

    wake = threading.Event()

    def forwarder():
        while True:
            forward_pending(store, env)
            wake.wait(FORWARD_EVERY_S)
            wake.clear()

    threading.Thread(target=forwarder, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store, env, on_apply=wake.set))
    log.info("listening on %s:%d/noop (receiverStateId %s)", args.host, args.port, store.receiver_state_id)
    server.serve_forever()


if __name__ == "__main__":
    main()
