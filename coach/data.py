"""Loading the raw dataset, athlete settings and coach_config.json."""
import datetime as dt
import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
DERIVED = DATA / "derived"
CONFIG_PATH = ROOT / "coach_config.json"

CYCLING = {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "EBikeRide",
           "EMountainBikeRide", "Velomobile", "Handcycle"}
RACE_WORKOUT_TYPE = 11  # Strava ride workout_type: 10 default, 11 race, 12 workout

DEFAULT_CONFIG = {
    # [{"from": "YYYY-MM-DD", "ftp": 300}, ...]; empty -> Strava profile FTP for all dates
    "ftp_history": [],
    # threshold HR for hrTSS on rides without power; null -> lower bound of Strava HR zone 4
    "lthr": None,
    # TSS/hour estimate for activities with neither power nor HR
    "fallback_tss_per_hour": {"WeightTraining": 30, "Pilates": 20, "Yoga": 15,
                              "AlpineSki": 40, "default": 50},
    "goals": {
        # base | threshold | vo2max | sweetspot | event
        "focus": "threshold",
        "event_name": None,
        "event_date": None,
    },
    "plan": {
        # Minutes available per weekday; null -> inferred from the last 8 weeks of riding
        "availability_min": None,
        "target_ctl_ramp_per_week": 4,
        "recovery_week_every": 4,
        "key_sessions_per_week": 2,
    },
}


def _merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


@lru_cache
def config():
    user = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    return _merge(DEFAULT_CONFIG, user)


@lru_cache
def athlete():
    return json.loads((DATA / "athlete.json").read_text())


def weight():
    return athlete()["profile"].get("weight")


def ftp_on(date: str) -> float:
    """FTP in effect on a YYYY-MM-DD date."""
    ftp = athlete()["profile"]["ftp"]
    for entry in sorted(config()["ftp_history"], key=lambda e: e["from"]):
        if entry["from"] <= date:
            ftp = entry["ftp"]
    return ftp


def hr_zone_bounds():
    """Lower bounds of Strava HR zones 2..5, e.g. [129, 160, 176, 192]."""
    return [z["min"] for z in athlete()["zones"]["heart_rate"]["zones"][1:]]


def lthr():
    return config()["lthr"] or hr_zone_bounds()[2]


def raw_files():
    return sorted(RAW.glob("*.json.gz"))


def load_raw(path_or_id):
    path = path_or_id if isinstance(path_or_id, Path) else RAW / f"{path_or_id}.json.gz"
    return json.loads(gzip.decompress(path.read_bytes()))


def streams(record):
    return {k: v["data"] for k, v in (record.get("streams") or {}).items()}


def activity(record):
    return record.get("detail") or record["summary"]


def description_metrics(act):
    """Head-unit summary the rider's device writes into the description, e.g.
    'TSS: 121.8 / AVG Power: 209.9 / Weighted AVG Power: 254'."""
    text = act.get("description") or ""
    out = {}
    for key, pattern in (("tss", r"TSS:\s*([\d.]+)"), ("avg_power", r"(?<!Weighted )AVG Power:\s*([\d.]+)"),
                         ("np", r"Weighted AVG Power:\s*([\d.]+)")):
        if mt := re.search(pattern, text):
            out[key] = float(mt.group(1))
    return out


def is_race(act):
    """Strava race flag, or "race" in the name (Zwift races are not flagged)."""
    return act.get("workout_type") == RACE_WORKOUT_TYPE or bool(re.search(r"\brace\b", act.get("name") or "", re.I))


def today():
    return dt.date.today()


def parse_day(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])
