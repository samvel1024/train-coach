"""Per-activity derived metrics, cached in data/derived/activities.jsonl.

Every coach tool calls ensure(), which recomputes only activities whose raw file,
FTP setting or metric code version changed.

  python3 -m coach.derive [--force]
"""
import argparse
import json

from . import data, metrics as m

VERSION = 7
CACHE = data.DERIVED / "activities.jsonl"


def series_1hz(record):
    """Resampled 1 Hz series for an activity: (series dict, segments, pos, median_dt)."""
    s = data.streams(record)
    time = s.get("time")
    if not time:
        return {}, [], [], None
    names = [k for k in ("watts", "heartrate", "cadence", "altitude", "velocity_smooth") if k in s]
    series, segments, pos = m.resample(time, {k: s[k] for k in names})
    return series, segments, pos, m.median_sample_interval(time)


def has_power(act, series):
    return bool(series.get("watts")) and act.get("device_watts") is not False and any(series["watts"])


def efforts_for(act, series, pos, ftp):
    """(efforts, structured). Manual laps win over stream detection."""
    efforts = m.lap_efforts(act.get("laps") or [], ftp)
    if efforts:
        for e in efforts:
            if e["start"] is not None and e["start"] < len(pos):
                e["start"] = pos[e["start"]]
        return efforts, m.label_sets(efforts, from_laps=True)
    efforts = m.detect_efforts(series["watts"], ftp, series.get("heartrate"), series.get("cadence"))
    # MTB terrain produces repeated surges that are not a planned workout.
    allow = act["sport_type"] != "MountainBikeRide"
    return efforts, m.label_sets(efforts, from_laps=False, allow_sets=allow)


def derive(record, src_mtime=None):
    act = data.activity(record)
    date = act["start_date_local"][:10]
    ftp = data.ftp_on(date)
    sport = act["sport_type"]
    cycling = sport in data.CYCLING
    series, segments, pos, median_dt = series_1hz(record)
    device = data.description_metrics(act)
    moving_s = segments[-1][1] if segments else act.get("moving_time") or 0
    row = {
        "id": act["id"], "_v": VERSION, "_src_mtime": src_mtime,
        "date": date, "start_local": act["start_date_local"], "name": act.get("name"),
        "sport_type": sport, "cycling": cycling,
        "indoor": sport == "VirtualRide" or bool(act.get("trainer")),
        "race": data.is_race(act),
        "distance_km": round((act.get("distance") or 0) / 1000, 2),
        "elevation_m": act.get("total_elevation_gain"), "elapsed_s": act.get("elapsed_time"),
        "moving_s": moving_s, "ftp": ftp, "median_dt": median_dt,
        "low_res": bool(median_dt and median_dt > 2),
        "strava_np": act.get("weighted_average_watts"),
        "device_tss": device.get("tss"),
        "has_power": False, "has_hr": False,
        "np": None, "avg_power": None, "if": None, "kj": None, "tss": None, "tss_source": None,
        "avg_hr": None, "max_hr": None, "ef": None, "decoupling_pct": None,
        "power_zone_s": None, "hr_zone_s": None, "coasting_s": None,
        "mmp": {}, "mmp_after_kj": {}, "best20_hr": None,
        "efforts": [], "structured": False, "category": None, "secondary": [],
    }

    power, hr = series.get("watts"), series.get("heartrate")
    if hr and any(hr):
        hrs = [h for h in hr if h > 0]
        row.update(has_hr=True, avg_hr=round(m.mean(hrs), 1), max_hr=max(hrs),
                   hr_zone_s=m.zone_seconds(hrs, data.hr_zone_bounds(), 5))

    if has_power(act, series):
        np_ = m.normalized_power(power)
        row.update(
            has_power=True, np=round(np_, 1), avg_power=round(m.mean(power), 1),
            kj=round(sum(power) / 1000), coasting_s=sum(1 for p in power if p <= 0),
            power_zone_s=m.zone_seconds(power, m.power_zone_limits(ftp), 7, skip_zero=True),
            mmp=m.mean_max(power, segments, low_res=row["low_res"]),
        )
        row["tss"], row["tss_source"] = round(m.tss(moving_s, np_, ftp), 1), "power"
        if row["low_res"] and row["device_tss"]:
            # Coarse streams inflate NP (few samples per 30 s window). The head unit computed
            # TSS from the full 1 Hz recording, so trust it and back out a consistent NP.
            row["tss"], row["tss_source"] = row["device_tss"], "device"
            row["np"] = round(ftp * (row["device_tss"] * 36 / moving_s) ** 0.5, 1)
        row["if"] = round(row["np"] / ftp, 3)
        row["mmp_after_kj"] = _mmp_after_kj(power, segments)
        start, _ = m.best_window(power, 1200, segments)
        if start is not None and hr:
            window = [h for h in hr[start:start + 1200] if h > 0]
            row["best20_hr"] = round(m.mean(window)) if window else None
        if row["has_hr"]:
            row["ef"] = round(m.efficiency_factor(row["np"], row["avg_hr"]), 3)
            d = m.decoupling(power, hr)
            row["decoupling_pct"] = round(d, 2) if d is not None else None
        row["efforts"], row["structured"] = efforts_for(act, series, pos, ftp)
    elif row["has_hr"]:
        row["tss"], row["tss_source"] = round(m.hr_tss(moving_s, row["avg_hr"], data.lthr()), 1), "hr"
    else:
        per_hour = data.config()["fallback_tss_per_hour"]
        rate = per_hour.get(sport, per_hour["default"])
        row["tss"], row["tss_source"] = round(moving_s / 3600 * rate, 1), "duration"

    row["category"], row["secondary"] = m.classify(
        sport, row["race"], row["if"], moving_s, row["efforts"], row["structured"], cycling)
    # Round-trip so fresh rows match cached ones (JSON object keys become strings, e.g. mmp["300"]).
    return json.loads(json.dumps(row))


def _mmp_after_kj(power, segments, thresholds=(1000, 2000, 3000), durations=(300, 1200)):
    """Best 5/20-min power after the first N kJ of work: fatigue resistance."""
    out, energy, i = {}, 0.0, 0
    for kj in thresholds:
        while i < len(power) and energy < kj * 1000:
            energy += power[i]
            i += 1
        if energy < kj * 1000:
            break
        clipped = [(max(s, i), e) for s, e in segments if e > i]
        vals = {d: round(v) for d in durations if (v := m.best_mean(power, d, clipped)) is not None}
        if vals:
            out[kj] = vals
    return out


def _load_cache():
    if not CACHE.exists():
        return {}
    return {r["id"]: r for r in map(json.loads, CACHE.read_text().splitlines()) if r}


def ensure(force=False, verbose=False):
    """Bring the cache up to date and return rows sorted by start time."""
    cache = {} if force else _load_cache()
    rows, changed = [], False
    for path in data.raw_files():
        aid = int(path.name.split(".")[0])
        mtime = path.stat().st_mtime
        row = cache.get(aid)
        if (row is None or row.get("_v") != VERSION or row.get("_src_mtime") != mtime
                or row.get("ftp") != data.ftp_on(row["date"])):
            row = derive(data.load_raw(path), mtime)
            changed = True
            if verbose:
                print(f"derived {row['date']} {row['name']}")
        rows.append(row)
    rows.sort(key=lambda r: r["start_local"])
    if changed or len(rows) != len(cache):
        data.DERIVED.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        tmp.replace(CACHE)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="recompute every activity")
    args = p.parse_args()
    rows = ensure(force=args.force, verbose=True)
    print(f"{len(rows)} activities in {CACHE.relative_to(data.ROOT)}")


if __name__ == "__main__":
    main()
