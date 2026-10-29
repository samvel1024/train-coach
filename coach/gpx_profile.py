"""Course profile from a GPX file: distance, climbing, climbs, and a per-segment profile.

  python3 -m coach.gpx_profile FILE.gpx [FILE2.gpx ...] [--seg-km 10] [--min-gain 25]
"""
import argparse
import math
import xml.etree.ElementTree as ET


def points(path):
    root = ET.parse(path).getroot()
    pts = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept"):
            ele = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "ele"), None)
            pts.append((float(el.get("lat")), float(el.get("lon")), float(ele) if ele else None))
    return pts


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def resample(pts, step=50.0):
    """Elevation every `step` metres along the track (linear interpolation)."""
    dist, ele = [0.0], [pts[0][2]]
    for a, b in zip(pts, pts[1:]):
        dist.append(dist[-1] + hav(a, b))
        ele.append(b[2])
    out, i, d = [], 0, 0.0
    while d <= dist[-1]:
        while dist[i + 1] < d:
            i += 1
        span = dist[i + 1] - dist[i] or 1
        out.append((d, ele[i] + (ele[i + 1] - ele[i]) * (d - dist[i]) / span))
        d += step
        if i + 1 >= len(dist) - 1 and d > dist[-1]:
            break
    return out


def smooth(prof, w=5):
    e = [p[1] for p in prof]
    return [(prof[k][0], sum(e[max(0, k - w):k + w + 1]) / len(e[max(0, k - w):k + w + 1])) for k in range(len(e))]


def climbs(prof, min_gain, max_dip=15.0):
    """Sections that gain >= min_gain m, tolerating dips of up to max_dip m."""
    out, start, low_i, high_i = [], 0, 0, 0
    for k, (_, e) in enumerate(prof):
        if e > prof[high_i][1]:
            high_i = k
        if prof[high_i][1] - e > max_dip or k == len(prof) - 1:
            gain = prof[high_i][1] - prof[low_i][1]
            if gain >= min_gain and high_i > low_i:
                out.append((prof[low_i][0], prof[high_i][0], gain))
            low_i = high_i = k
        if e < prof[low_i][1]:
            low_i = high_i = k
    return out


def report(path, seg_km, min_gain):
    pts = [p for p in points(path) if p[2] is not None]
    prof = smooth(resample(pts))
    total = prof[-1][0]
    up = sum(max(0, b[1] - a[1]) for a, b in zip(prof, prof[1:]))
    down = sum(max(0, a[1] - b[1]) for a, b in zip(prof, prof[1:]))
    els = [e for _, e in prof]
    print(f"\n== {path.rsplit('/', 1)[-1]}")
    print(f"{total / 1000:.1f} km  +{up:.0f} m / -{down:.0f} m  ({up / total * 1000:.1f} m/km)  "
          f"elev {min(els):.0f}-{max(els):.0f} m (start {els[0]:.0f}, finish {els[-1]:.0f})")
    print(f"start {pts[0][0]:.4f},{pts[0][1]:.4f}  finish {pts[-1][0]:.4f},{pts[-1][1]:.4f}")
    print("climbs:")
    for a, b, g in climbs(prof, min_gain) or []:
        print(f"  km {a / 1000:5.1f}-{b / 1000:5.1f}  {(b - a) / 1000:4.1f} km  +{g:3.0f} m  {g / (b - a) * 100:4.1f}%")
    print(f"profile per {seg_km:g} km:")
    step = seg_km * 1000
    k0 = 0
    while k0 < len(prof) - 1:
        seg = [p for p in prof if prof[k0][0] <= p[0] <= prof[k0][0] + step]
        s_up = sum(max(0, b[1] - a[1]) for a, b in zip(seg, seg[1:]))
        net = seg[-1][1] - seg[0][1]
        print(f"  km {seg[0][0] / 1000:5.1f}-{seg[-1][0] / 1000:5.1f}  {seg[0][1]:5.0f}->{seg[-1][1]:5.0f} m  "
              f"net {net:+5.0f}  up +{s_up:4.0f}  avg {net / max(1, seg[-1][0] - seg[0][0]) * 100:+4.1f}%")
        k0 += len(seg) - 1 if len(seg) > 1 else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+")
    p.add_argument("--seg-km", type=float, default=10)
    p.add_argument("--min-gain", type=float, default=25)
    a = p.parse_args()
    for f in a.files:
        report(f, a.seg_km, a.min_gain)


if __name__ == "__main__":
    main()
