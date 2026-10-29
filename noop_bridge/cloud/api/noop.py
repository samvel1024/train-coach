"""Vercel Function: stateless NOOP push receiver -> intervals.icu wellness.

Serves https://<project>.vercel.app/api/noop. Each dailyMetric replace-window batch is
written to intervals.icu before it is acknowledged, so intervals.icu is the durable store:
a failed write returns 503 and NOOP retries the identical batch later. Writes are per-day
upserts, which makes retries harmless and removes the need for receiver-side state.

A 14-day dailyMetric window is always a single part (far below the 5,000-record limit), so
multi-part replacements, which would need staging storage, are refused with 422.

Environment: NOOP_TOKEN, INTERVALS_API_KEY, INTERVALS_ATHLETE_ID, RECEIVER_STATE_ID (UUID).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from noop_protocol import (MAX_WIRE, ProtocolError, ack_for, authorized, capabilities_doc,  # noqa: E402
                           decode_body, error_doc, parse_batch, put_wellness, to_wellness)

PATH = "/api/noop"


def config():
    return {k: os.environ.get(k, "") for k in
            ("NOOP_TOKEN", "INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID", "RECEIVER_STATE_ID")}


def deliver(entity, env, put=None):
    """Forward one batch to intervals.icu and return the acknowledgement."""
    put = put or put_wellness
    header, records = parse_batch(entity)
    if header["window"]["parts"] != 1:
        raise ProtocolError(422, "multipart_unsupported")
    for r in records:
        wellness = to_wellness(r["day"], r["data"])
        if len(wellness) > 1:
            try:
                put(env, wellness)
            except Exception as e:  # any upstream failure is retryable for NOOP
                print(f"intervals.icu write failed for {r['day']}: {e!r}", file=sys.stderr)
                raise ProtocolError(503, "upstream_unavailable")
    return ack_for(header)


class handler(BaseHTTPRequestHandler):
    def _send(self, status, obj):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, env):
        if self.path.split("?")[0].rstrip("/") != PATH:
            self._send(404, error_doc("not_found"))
            return False
        if not all(env.values()):
            self._send(500, error_doc("receiver_not_configured"))
            return False
        if not authorized(self.headers.get("Authorization"), env["NOOP_TOKEN"]):
            self._send(401, error_doc("unauthorized"))
            return False
        return True

    def do_GET(self):
        env = config()
        if not self._guard(env):
            return
        try:
            self._send(200, capabilities_doc(env["RECEIVER_STATE_ID"], self.headers.get("NOOP-Push-Accept-Version")))
        except ProtocolError as e:
            self._send(e.status, error_doc(e.code))

    def do_POST(self):
        env = config()
        if not self._guard(env):
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0:
                raise ProtocolError(411, "length_required")
            if length > MAX_WIRE:
                raise ProtocolError(413, "too_large")
            entity = decode_body(self.rfile.read(length), self.headers.get("Content-Encoding"))
            ack = deliver(entity, env)
        except ProtocolError as e:
            self._send(e.status, error_doc(e.code))
            return
        except Exception as e:
            print(f"receiver failure: {e!r}", file=sys.stderr)
            self._send(500, error_doc("receiver_failure"))
            return
        self._send(200, ack)
