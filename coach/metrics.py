"""Pure training metrics on 1 Hz series. No I/O here, so everything is unit-testable."""
import bisect

# Coggan power zone upper bounds as fraction of FTP for Z1..Z6 (Z7 is open-ended)
POWER_ZONE_BOUNDS = (0.55, 0.75, 0.90, 1.05, 1.20, 1.50)
POWER_ZONE_NAMES = ("Z1 recovery", "Z2 endurance", "Z3 tempo", "Z4 threshold",
                    "Z5 VO2max", "Z6 anaerobic", "Z7 neuromuscular")
MMP_DURATIONS = (5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600, 5400, 7200)
SHORT_DURATION_LIMIT = 300  # peaks shorter than this are inflated on coarse (4-8 s) recordings
MAX_GAP = 10  # seconds between samples that are still treated as continuous riding
INTENSITY_ORDER = ("anaerobic", "vo2max", "threshold", "sweetspot", "tempo")
MIN_WORK_S = {"anaerobic": 60, "vo2max": 240, "threshold": 600, "sweetspot": 900, "tempo": 1200}


# ---------------------------------------------------------------- resampling

def resample(time, series, max_gap=MAX_GAP):
    """Resample irregular samples onto a 1 Hz grid of moving time.

    Each sample's value is held until the next sample. Gaps longer than max_gap
    (auto-pause, stops) are cut out and start a new segment. None becomes 0.
    Returns (dict name -> list, segments [(start, end)] as indices into the lists,
    pos: resampled index of every raw sample, for mapping lap start_index values).
    """
    out = {k: [] for k in series}
    segments, seg_start, length, pos = [], 0, 0, []
    n = len(time)
    for i in range(n):
        pos.append(length)
        gap = time[i + 1] - time[i] if i + 1 < n else 1
        if gap <= 0:
            continue
        reps = gap if gap <= max_gap else 1
        for k, values in series.items():
            v = values[i]
            out[k].extend([0 if v is None else v] * reps)
        length += reps
        if gap > max_gap:
            segments.append((seg_start, length))
            seg_start = length
    if length > seg_start:
        segments.append((seg_start, length))
    return out, segments, pos


def median_sample_interval(time):
    gaps = sorted(b - a for a, b in zip(time, time[1:]) if b > a)
    return gaps[len(gaps) // 2] if gaps else None


# ---------------------------------------------------------------- basics

def mean(xs):
    return sum(xs) / len(xs) if xs else None


def prefix_sums(xs):
    out, acc = [0.0], 0.0
    for x in xs:
        acc += x
        out.append(acc)
    return out


def rolling_mean(xs, window):
    """Trailing rolling mean; result[i] averages xs[i:i+window]."""
    if len(xs) < window:
        return []
    p = prefix_sums(xs)
    return [(p[i + window] - p[i]) / window for i in range(len(xs) - window + 1)]


def centered_mean(xs, window):
    """Same length as xs; edges use the partial window."""
    p = prefix_sums(xs)
    half, n = window // 2, len(xs)
    return [(p[min(n, i + half + 1)] - p[max(0, i - half)]) / (min(n, i + half + 1) - max(0, i - half))
            for i in range(n)]


def normalized_power(power):
    if not power:
        return None
    rolled = rolling_mean(power, 30) or [mean(power)]
    return mean([r ** 4 for r in rolled]) ** 0.25


def tss(seconds, np_, ftp):
    if not np_ or not ftp:
        return 0.0
    return seconds * np_ * (np_ / ftp) / (ftp * 3600) * 100


def hr_tss(seconds, avg_hr, lthr):
    return seconds / 3600 * (avg_hr / lthr) ** 2 * 100


# ---------------------------------------------------------------- zones

def power_zone_limits(ftp):
    return [b * ftp for b in POWER_ZONE_BOUNDS]


def zone_seconds(values, lower_bounds, n_zones, skip_zero=False):
    """Seconds per zone; lower_bounds are the lower edges of zones 2..n."""
    out = [0] * n_zones
    for v in values:
        if skip_zero and v <= 0:
            continue
        out[bisect.bisect_right(lower_bounds, v)] += 1
    return out


def three_zone(power_zone_s):
    """Collapse 7 Coggan zones into low (Z1-2) / mid (Z3-4) / high (Z5+) fractions."""
    total = sum(power_zone_s) or 1
    return (sum(power_zone_s[:2]) / total, sum(power_zone_s[2:4]) / total, sum(power_zone_s[4:]) / total)


def distribution_model(low, mid, high):
    if low >= 0.75 and high > mid:
        return "polarized"
    if low > mid > high:
        return "pyramidal"
    if mid >= low:
        return "threshold-heavy"
    return "mixed"


# ---------------------------------------------------------------- mean-max power

def best_mean(xs, window, segments):
    """Best average over `window` consecutive seconds that stay within one segment."""
    p = prefix_sums(xs)
    best = None
    for s, e in segments:
        if e - s < window:
            continue
        m = max(p[i + window] - p[i] for i in range(s, e - window + 1)) / window
        best = m if best is None or m > best else best
    return best


def best_window(xs, window, segments):
    """(start, mean) of the best window, or (None, None)."""
    p = prefix_sums(xs)
    best = (None, None)
    for s, e in segments:
        for i in range(s, e - window + 1):
            m = (p[i + window] - p[i]) / window
            if best[1] is None or m > best[1]:
                best = (i, m)
    return best


def mean_max(power, segments, durations=MMP_DURATIONS, low_res=False):
    out = {}
    for d in durations:
        if low_res and d < SHORT_DURATION_LIMIT:
            continue
        v = best_mean(power, d, segments)
        if v is not None:
            out[d] = round(v, 1)
    return out


def cp_fit(mmp, durations=(180, 300, 600, 1200)):
    """Critical power and W' from the linear work-time model W = CP*t + W'.

    mmp: {duration_s: watts}. Uses whichever of `durations` are present (>=2 needed).
    """
    pts = [(d, mmp[d] * d) for d in durations if mmp.get(d)]
    if len(pts) < 2:
        return None, None
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mw = sum(w for _, w in pts) / n
    var = sum((t - mt) ** 2 for t, _ in pts)
    cp = sum((t - mt) * (w - mw) for t, w in pts) / var
    return cp, mw - cp * mt


def w_prime_balance(power, cp, w_prime):
    """Skiba differential W' balance, joules remaining each second."""
    bal, out = w_prime, []
    for p in power:
        if p > cp:
            bal -= p - cp
        else:
            bal += (w_prime - bal) * (cp - p) / w_prime
        out.append(bal)
    return out


# ---------------------------------------------------------------- aerobic

def efficiency_factor(np_, avg_hr):
    return np_ / avg_hr if np_ and avg_hr else None


def decoupling(power, hr, warmup_s=600, max_power_diff=0.15):
    """Pa:HR decoupling in % between the two halves of the ride; positive = HR drifted up.

    Uses pedaling samples with HR after the warm-up. Only meaningful for steady rides, so
    returns None when the halves' average power differs by more than max_power_diff.
    """
    pairs = [(p, h) for p, h in zip(power[warmup_s:], hr[warmup_s:]) if h > 0 and p > 0]
    if len(pairs) < 1200:
        return None
    half = len(pairs) // 2
    p1, p2 = mean([p for p, _ in pairs[:half]]), mean([p for p, _ in pairs[half:]])
    if abs(p1 - p2) > max_power_diff * p1:
        return None
    r1 = p1 / mean([h for _, h in pairs[:half]])
    r2 = p2 / mean([h for _, h in pairs[half:]])
    return (r1 - r2) / r1 * 100


# ---------------------------------------------------------------- efforts

def effort_category(pct, duration):
    if pct >= 1.20:
        return "anaerobic" if duration < 60 else "vo2max"
    if pct >= 1.05:
        return "vo2max"
    if pct >= 0.95:
        return "threshold"
    if pct >= 0.88:
        return "sweetspot"
    if pct >= 0.76:
        return "tempo"
    return "endurance"


def _runs(mask, merge_gap):
    runs, start, last = [], None, None
    for i, m in enumerate(mask):
        if m:
            if start is None:
                start = i
            elif i - last - 1 > merge_gap:
                runs.append((start, last + 1))
                start = i
            last = i
    if start is not None:
        runs.append((start, last + 1))
    return runs


def _effort(start, end, power, hr, cadence, ftp, source):
    seg = power[start:end]
    hrs = [h for h in hr[start:end] if h > 0] if hr else []
    cads = [c for c in cadence[start:end] if c > 0] if cadence else []
    avg = mean(seg)
    return {
        "start": start, "duration": end - start, "avg_power": round(avg),
        "pct_ftp": round(avg / ftp, 3), "category": effort_category(avg / ftp, end - start),
        "avg_hr": round(mean(hrs)) if hrs else None, "max_hr": max(hrs) if hrs else None,
        "avg_cadence": round(mean(cads)) if cads else None, "source": source,
    }


def detect_efforts(power, ftp, hr=None, cadence=None):
    """Find hard efforts in a 1 Hz power series.

    Sustained: centered 30 s power >= 85% FTP for >= 2 min (gaps <= 20 s merged).
    Short: centered 10 s power >= 120% FTP for 10-119 s, outside sustained efforts.
    """
    if not power:
        return []
    s30 = centered_mean(power, 30)
    sustained = [(s, e) for s, e in _runs([v >= 0.85 * ftp for v in s30], 20) if e - s >= 120]
    covered = set()
    for s, e in sustained:
        covered.update(range(s, e))
    s10 = centered_mean(power, 10)
    short = [(s, e) for s, e in _runs([v >= 1.20 * ftp for v in s10], 5)
             if 10 <= e - s < 120 and not covered.intersection(range(s, e))]
    return [_effort(s, e, power, hr, cadence, ftp, "streams") for s, e in sorted(sustained + short)]


def lap_efforts(laps, ftp):
    """Work intervals from manual laps (needs >= 3 laps).

    A lap is work if it lasts 20 s - 45 min and averages >= 85% FTP,
    or lasts >= 10 min at >= 78% FTP (tempo blocks).
    """
    if len(laps) < 3:
        return []
    out = []
    for lap in laps:
        w, d = lap.get("average_watts") or 0, lap.get("elapsed_time") or 0
        if 20 <= d <= 2700 and (w >= 0.85 * ftp or (d >= 600 and w >= 0.78 * ftp)):
            out.append({
                "start": lap.get("start_index"), "duration": d, "avg_power": round(w),
                "pct_ftp": round(w / ftp, 3), "category": effort_category(w / ftp, d),
                "avg_hr": round(lap["average_heartrate"]) if lap.get("average_heartrate") else None,
                "max_hr": round(lap["max_heartrate"]) if lap.get("max_heartrate") else None,
                "avg_cadence": round(lap["average_cadence"]) if lap.get("average_cadence") else None,
                "source": "laps", "lap_index": lap.get("lap_index"),
            })
    return out


def group_efforts(efforts, dur_tol=0.25, power_tol=None):
    """Greedy clustering of efforts into repeats of similar duration (and power, if power_tol)."""
    groups = []
    for e in efforts:
        for g in groups:
            gd, gp = mean([x["duration"] for x in g]), mean([x["avg_power"] for x in g])
            if abs(e["duration"] - gd) <= dur_tol * max(e["duration"], gd) and (
                    power_tol is None or abs(e["avg_power"] - gp) <= power_tol * max(e["avg_power"], gp)):
                g.append(e)
                break
        else:
            groups.append([e])
    return groups


def label_sets(efforts, from_laps, allow_sets=True):
    """Group efforts into sets, give each set one category from its mean, return `structured`.

    Each effort gets "set" (index or None) and its set's "category". Lap efforts were marked
    by the rider, so all of them count. Stream efforts only count when they repeat:
    >= 3 efforts of >= 30 s, or >= 2 of >= 5 min, at similar power (+-10%). That keeps
    random outdoor surges and climbs from turning an endurance ride into a "workout".
    """
    groups = group_efforts(efforts, power_tol=None if from_laps else 0.10)
    structured = False
    for i, g in enumerate(groups):
        d = mean([e["duration"] for e in g])
        is_set = allow_sets and (from_laps or (len(g) >= 3 and d >= 30) or (len(g) >= 2 and d >= 300))
        cat = effort_category(mean([e["pct_ftp"] for e in g]), d)
        for e in g:
            e["category"], e["set"] = cat, i if is_set else None
        structured = structured or is_set
    return structured


def work_by_category(efforts):
    out = {}
    for e in efforts:
        out[e["category"]] = out.get(e["category"], 0) + e["duration"]
    return out


def classify(sport_type, race, if_, moving_s, efforts, structured, is_cycling=True):
    """Return (primary category, [secondary categories])."""
    if not is_cycling:
        return ("strength" if sport_type == "WeightTraining" else "cross-training"), []
    if race:
        return "race", []
    work = work_by_category([e for e in efforts if e.get("set") is not None]) if structured else {}
    hit = [c for c in INTENSITY_ORDER if work.get(c, 0) >= MIN_WORK_S[c]]
    if hit:
        return hit[0], hit[1:]
    if if_ is None:
        return "endurance", []
    if if_ < 0.60 and moving_s < 90 * 60:
        return "recovery", []
    if if_ < 0.82:
        return "endurance", []
    if if_ < 0.90:
        return "tempo", []
    return "hard-unstructured", []


def summarize_efforts(efforts):
    """'4x10:13 @ 322W (102%)' style text; groups consecutive similar efforts."""
    groups = []
    for e in efforts:
        g = groups[-1] if groups else None
        if g and abs(g[0]["duration"] - e["duration"]) <= 0.25 * max(g[0]["duration"], e["duration"]) \
                and g[0]["category"] == e["category"]:
            g.append(e)
        else:
            groups.append([e])
    parts = []
    for g in groups:
        d = mean([e["duration"] for e in g])
        w = mean([e["avg_power"] for e in g])
        pct = mean([e["pct_ftp"] for e in g])
        parts.append(f"{len(g)}x{fmt_dur(d)} @ {w:.0f}W ({pct:.0%})")
    return ", ".join(parts)


def fmt_dur(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}"
    return f"{m}:{s:02d}" if m else f"{s}s"


def linear_slope(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var else None


def pmc_step(ctl, atl, load):
    """One day of the performance-management chart (42 d / 7 d exponential averages)."""
    return ctl + (load - ctl) / 42, atl + (load - atl) / 7


def daily_load_for_ramp(ctl, ramp_per_week):
    """Constant daily TSS that raises CTL by ramp_per_week over 7 days."""
    k = 1 - (41 / 42) ** 7
    return ctl + ramp_per_week / k
