"""NOOP push receiver: protocol behaviour over real HTTP, plus the intervals.icu mapping."""
import gzip
import json
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

from noop_bridge import receiver as rx

TOKEN = "test-token"
ENV = {"NOOP_TOKEN": TOKEN, "INTERVALS_API_KEY": "k", "INTERVALS_ATHLETE_ID": "i1"}
SRC, DEV = str(uuid.uuid4()), "strap-1"


def day_row(day, **data):
    base = {"restingHr": 48, "avgHrv": 82.4, "totalSleepMin": 452, "recovery": 71, "spo2Pct": 96.5,
            "respRateBpm": 14.2, "steps": 9123}
    base.update(data)
    return {"type": "record", "key": {"day": day}, "data": base}


def batch(records, start="2026-10-01", end="2026-10-15", part=1, parts=1, rid=None, batch_id=None,
          stream="dailyMetric"):
    header = {"type": "batch", "protocolVersion": "1.0", "batchId": batch_id or str(uuid.uuid4()),
              "sourceId": SRC, "deviceId": DEV, "stream": stream, "delivery": "replace_window",
              "recordCount": len(records), "startCursor": None, "endCursor": None,
              "window": {"replacementId": rid or str(uuid.uuid4()), "selector": "day", "startInclusive": start,
                         "endExclusive": end, "part": part, "parts": parts}}
    return ("\n".join(json.dumps(x) for x in [header, *records]) + "\n").encode(), header


class Receiver(unittest.TestCase):
    def setUp(self):
        self.store = rx.Store(":memory:")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), rx.make_handler(self.store, ENV))
        self.url = f"http://127.0.0.1:{self.server.server_port}/noop"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, body=None, token=TOKEN, gzip_body=True, url=None, headers=None):
        h = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
        if body is not None:
            h["Content-Type"] = "application/x-ndjson; charset=utf-8"
            if gzip_body:
                body = gzip.compress(body)
                h["Content-Encoding"] = "gzip"
        req = urllib.request.Request(url or self.url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def days(self):
        return {d: json.loads(v) for d, v in self.store.db.execute("SELECT day, data FROM daily")}

    def test_capabilities(self):
        status, caps = self.call("GET", headers={"NOOP-Push-Accept-Version": "1.0"})
        self.assertEqual(status, 200)
        self.assertEqual(caps["protocolVersion"], "1.0")
        self.assertEqual(caps["streams"], ["dailyMetric"])
        uuid.UUID(caps["receiverStateId"])
        self.assertEqual(self.call("GET", headers={"NOOP-Push-Accept-Version": "2.0"})[0], 406)

    def test_auth_and_path(self):
        self.assertEqual(self.call("GET", token="wrong")[0], 401)
        self.assertEqual(self.call("GET", url=self.url + "x")[0], 404)

    def test_single_part_window_and_exact_ack(self):
        body, h = batch([day_row("2026-10-05"), day_row("2026-10-06", restingHr=51)])
        status, ack = self.call("POST", body)
        self.assertEqual(status, 200)
        self.assertEqual(ack, {"protocolVersion": "1.0", "batchId": h["batchId"], "stream": "dailyMetric",
                               "deviceId": DEV, "endCursor": None, "acceptedRows": 2, "status": "accepted"})
        self.assertEqual(self.days()["2026-10-06"]["restingHr"], 51)

    def test_identity_encoding_accepted(self):
        body, _ = batch([day_row("2026-10-05")])
        self.assertEqual(self.call("POST", body, gzip_body=False)[0], 200)

    def test_retry_is_idempotent_and_conflict_rejected(self):
        body, h = batch([day_row("2026-10-05")])
        first = self.call("POST", body)
        self.assertEqual(self.call("POST", body), first)
        other, _ = batch([day_row("2026-10-05", restingHr=60)], batch_id=h["batchId"])
        self.assertEqual(self.call("POST", other)[0], 409)
        self.assertEqual(self.days()["2026-10-05"]["restingHr"], 48)

    def test_absent_days_in_window_are_deleted(self):
        self.call("POST", batch([day_row("2026-10-05"), day_row("2026-10-06")])[0])
        self.call("POST", batch([day_row("2026-10-06")])[0])
        self.assertEqual(set(self.days()), {"2026-10-06"})
        # Rows outside the window are untouched
        self.call("POST", batch([day_row("2026-09-01")], start="2026-09-01", end="2026-09-02")[0])
        self.call("POST", batch([], start="2026-10-01", end="2026-10-15")[0])
        self.assertEqual(set(self.days()), {"2026-09-01"})

    def test_multi_part_applies_only_when_complete(self):
        rid = str(uuid.uuid4())
        p2, _ = batch([day_row("2026-10-07")], part=2, parts=2, rid=rid)
        self.assertEqual(self.call("POST", p2)[0], 200)
        self.assertEqual(self.days(), {})
        p1, _ = batch([day_row("2026-10-06")], part=1, parts=2, rid=rid)
        self.assertEqual(self.call("POST", p1)[0], 200)
        self.assertEqual(set(self.days()), {"2026-10-06", "2026-10-07"})
        late, _ = batch([day_row("2026-10-08")], part=1, parts=2, rid=rid)
        self.assertEqual(self.call("POST", late)[0], 409)

    def test_superseded_generation_rejected(self):
        old = str(uuid.uuid4())
        self.call("POST", batch([day_row("2026-10-06")], part=1, parts=2, rid=old)[0])
        self.call("POST", batch([day_row("2026-10-06")], rid=str(uuid.uuid4()))[0])
        late, _ = batch([day_row("2026-10-07")], part=2, parts=2, rid=old)
        self.assertEqual(self.call("POST", late)[0], 409)

    def test_validation(self):
        self.assertEqual(self.call("POST", batch([day_row("2026-10-05")], stream="hrSample")[0])[0], 422)
        self.assertEqual(self.call("POST", batch([day_row("2026-11-01")])[0])[0], 422)  # outside window
        dup, _ = batch([day_row("2026-10-05"), day_row("2026-10-05")])
        self.assertEqual(self.call("POST", dup)[0], 400)
        body, _ = batch([day_row("2026-10-05")])
        self.assertEqual(self.call("POST", body.replace(b'"recordCount": 1', b'"recordCount": 2'))[0], 400)
        self.assertEqual(self.call("POST", b"not json\n")[0], 400)
        self.assertEqual(self.days(), {})


class Forwarding(unittest.TestCase):
    def test_mapping(self):
        w = rx.to_wellness("2026-10-06", day_row("x")["data"])
        self.assertEqual(w, {"id": "2026-10-06", "restingHR": 48, "hrv": 82.4, "sleepSecs": 27120,
                             "readiness": 71, "spO2": 96.5, "respiration": 14.2, "steps": 9123})
        self.assertEqual(rx.to_wellness("2026-10-06", {"restingHr": None, "avgHrv": None}), {"id": "2026-10-06"})

    def test_forward_once_and_retry_after_failure(self):
        store = rx.Store(":memory:")
        rx.apply_window(store.db, SRC, DEV, {"startInclusive": "2026-10-01", "endExclusive": "2026-10-15"},
                        [{"day": "2026-10-05", "data": day_row("x")["data"]},
                         {"day": "2026-10-06", "data": day_row("x", restingHr=55)["data"]}])
        sent, offline = [], {"2026-10-06"}

        def flaky(env, w):
            if w["id"] in offline:
                raise urllib.error.URLError("offline")
            sent.append(w)

        self.assertEqual(rx.forward_pending(store, ENV, put=flaky), 1)  # 05 sent, 06 failed
        offline.clear()
        self.assertEqual(rx.forward_pending(store, ENV, put=flaky), 1)  # 06 retried
        self.assertEqual(sent[-1]["restingHR"], 55)
        self.assertEqual(rx.forward_pending(store, ENV, put=flaky), 0)  # nothing pending
        # A changed day is forwarded again
        rx.apply_window(store.db, SRC, DEV, {"startInclusive": "2026-10-06", "endExclusive": "2026-10-07"},
                        [{"day": "2026-10-06", "data": day_row("x", restingHr=57)["data"]}])
        self.assertEqual(rx.forward_pending(store, ENV, put=flaky), 1)


if __name__ == "__main__":
    unittest.main()


class CloudFunction(unittest.TestCase):
    """The Vercel handler, served locally by the same BaseHTTPRequestHandler class Vercel runs."""

    def setUp(self):
        import importlib.util
        import os
        from unittest import mock
        spec = importlib.util.spec_from_file_location("cloud_noop", rx.HERE / "cloud" / "api" / "noop.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.env = {**ENV, "RECEIVER_STATE_ID": str(uuid.uuid4())}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        self.sent, self.fail = [], False

        def fake_put(env, w):
            if self.fail:
                raise urllib.error.URLError("down")
            self.sent.append(w)

        self.put_patch = mock.patch.object(self.mod, "put_wellness", fake_put)
        self.put_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.mod.handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/noop"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.put_patch.stop()
        self.patch.stop()

    call = Receiver.call

    def test_capabilities_use_configured_state_id(self):
        status, caps = self.call("GET")
        self.assertEqual((status, caps["receiverStateId"], caps["streams"]),
                         (200, self.env["RECEIVER_STATE_ID"], ["dailyMetric"]))
        self.assertEqual(self.call("GET", token="nope")[0], 401)

    def test_forwards_then_acks(self):
        body, h = batch([day_row("2026-10-05"), day_row("2026-10-06", restingHr=50)])
        status, ack = self.call("POST", body)
        self.assertEqual((status, ack["batchId"], ack["acceptedRows"], ack["endCursor"]), (200, h["batchId"], 2, None))
        self.assertEqual([w["id"] for w in self.sent], ["2026-10-05", "2026-10-06"])
        self.assertEqual(self.sent[1]["restingHR"], 50)

    def test_upstream_failure_is_retryable_and_retry_is_safe(self):
        body, _ = batch([day_row("2026-10-05")])
        self.fail = True
        self.assertEqual(self.call("POST", body)[0], 503)
        self.fail = False
        self.assertEqual(self.call("POST", body)[0], 200)
        self.assertEqual(self.call("POST", body)[0], 200)  # identical retry: same upsert again
        self.assertEqual(len(self.sent), 2)

    def test_rejects_multipart_and_other_streams(self):
        self.assertEqual(self.call("POST", batch([day_row("2026-10-05")], part=1, parts=2)[0])[0], 422)
        self.assertEqual(self.call("POST", batch([day_row("2026-10-05")], stream="hrSample")[0])[0], 422)
        self.assertEqual(self.sent, [])

    def test_empty_window_acks_without_writes(self):
        self.assertEqual(self.call("POST", batch([])[0])[0], 200)
        self.assertEqual(self.sent, [])
