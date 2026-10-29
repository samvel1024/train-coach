"""NOOP self-hosted push protocol v1.0, dailyMetric stream -> intervals.icu wellness.

Pure protocol logic shared by the Mac receiver (../receiver.py) and the Vercel function
(api/noop.py). Spec: https://github.com/ryanbr/noop/blob/main/docs/PUSH_PROTOCOL.md
"""
import base64
import gzip
import hmac
import io
import json
import urllib.request

PROTOCOL = "1.0"
STREAMS = ["dailyMetric"]
MAX_DECODED = 4 * 1024 * 1024
MAX_WIRE = MAX_DECODED + 64 * 1024
DAILY_FIELDS = ("totalSleepMin", "efficiency", "deepMin", "remMin", "lightMin", "disturbances", "restingHr",
                "avgHrv", "recovery", "strain", "exerciseCount", "spo2Pct", "skinTempDevC", "respRateBpm",
                "steps", "activeKcalEst", "spo2Red", "spo2Ir")
INTERVALS_API = "https://intervals.icu/api/v1"


class ProtocolError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code




def authorized(authorization_header, token):
    return bool(token) and hmac.compare_digest((authorization_header or "").encode(), f"Bearer {token}".encode())


def capabilities_doc(receiver_state_id, accept_header):
    offered = [v.strip() for v in (accept_header or PROTOCOL).split(",")]
    if PROTOCOL not in offered:
        raise ProtocolError(406, "unsupported_version")
    return {"type": "capabilities", "protocolVersion": PROTOCOL,
            "receiverStateId": receiver_state_id, "streams": STREAMS}


def error_doc(code):
    return {"type": "error", "protocolVersion": PROTOCOL, "code": code}


def decode_body(raw: bytes, content_encoding):
    """Decoded NDJSON entity from the wire body (gzip or identity)."""
    if len(raw) > MAX_WIRE:
        raise ProtocolError(413, "too_large")
    coding = (content_encoding or "identity").lower()
    if coding == "gzip":
        try:
            body = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_DECODED + 1)
        except (OSError, EOFError):
            raise ProtocolError(400, "bad_gzip")
    elif coding == "identity":
        body = raw
    else:
        raise ProtocolError(415, "unsupported_encoding")
    if len(body) > MAX_DECODED:
        raise ProtocolError(413, "too_large")
    return body


def parse_batch(entity: bytes):
    if len(entity) > MAX_DECODED:
        raise ProtocolError(413, "too_large")
    try:
        text = entity.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(400, "bad_encoding")
    if not text.endswith("\n") or "\n\n" in text or text.startswith("﻿"):
        raise ProtocolError(400, "bad_ndjson")
    try:
        lines = [json.loads(line) for line in text[:-1].split("\n")]
    except json.JSONDecodeError:
        raise ProtocolError(400, "bad_ndjson")
    header, records = lines[0], lines[1:]
    if not isinstance(header, dict) or header.get("type") != "batch":
        raise ProtocolError(400, "bad_header")
    if header.get("protocolVersion") != PROTOCOL:
        raise ProtocolError(422, "unsupported_version")
    for k in ("batchId", "sourceId", "deviceId", "stream", "delivery", "recordCount"):
        if k not in header:
            raise ProtocolError(400, "bad_header")
    if header["stream"] not in STREAMS:
        raise ProtocolError(422, "unsupported_stream")
    if header["delivery"] != "replace_window" or not isinstance(header.get("window"), dict):
        raise ProtocolError(422, "bad_delivery")
    if header.get("startCursor") is not None or header.get("endCursor") is not None:
        raise ProtocolError(400, "bad_cursor")
    if header["recordCount"] != len(records):
        raise ProtocolError(400, "record_count_mismatch")
    w = header["window"]
    for k in ("replacementId", "selector", "startInclusive", "endExclusive", "part", "parts"):
        if k not in w:
            raise ProtocolError(400, "bad_window")
    if w["selector"] != "day" or not (1 <= w["part"] <= w["parts"]):
        raise ProtocolError(400, "bad_window")
    days, out = set(), []
    for r in records:
        if not isinstance(r, dict) or r.get("type") != "record" or set(r.get("key", {})) != {"day"}:
            raise ProtocolError(422, "bad_record")
        day = r["key"]["day"]
        if not (w["startInclusive"] <= day < w["endExclusive"]):
            raise ProtocolError(422, "record_outside_window")
        if day in days:
            raise ProtocolError(400, "duplicate_key")
        days.add(day)
        data = r.get("data") or {}
        out.append({"day": day, "data": {k: data.get(k) for k in DAILY_FIELDS}})
    return header, out



def ack_for(header):
    return {"protocolVersion": PROTOCOL, "batchId": header["batchId"], "stream": header["stream"],
            "deviceId": header["deviceId"], "endCursor": None, "acceptedRows": header["recordCount"],
            "status": "accepted"}



def to_wellness(day, d):
    """Map a NOOP dailyMetric row to intervals.icu wellness fields; None values are omitted."""
    w = {
        "id": day,
        "restingHR": d.get("restingHr"),
        "hrv": d.get("avgHrv"),
        "sleepSecs": round(d["totalSleepMin"] * 60) if d.get("totalSleepMin") is not None else None,
        "readiness": d.get("recovery"),
        "spO2": d.get("spo2Pct"),
        "respiration": d.get("respRateBpm"),
        "steps": d.get("steps"),
    }
    for k in ("restingHR", "readiness", "steps"):
        if w[k] is not None:
            w[k] = round(w[k])
    if w["hrv"] is not None:
        w["hrv"] = round(w["hrv"], 1)
    return {k: v for k, v in w.items() if v is not None}



def put_wellness(env, wellness):
    url = f"{INTERVALS_API}/athlete/{env['INTERVALS_ATHLETE_ID']}/wellness/{wellness['id']}"
    auth = base64.b64encode(f"API_KEY:{env['INTERVALS_API_KEY']}".encode()).decode()
    req = urllib.request.Request(url, data=json.dumps(wellness).encode(), method="PUT",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json",
                                          # intervals.icu's Cloudflare rejects Python's default UA (error 1010)
                                          "User-Agent": "noop-bridge/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
