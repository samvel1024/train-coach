---
name: intensity-distribution
description: Time in power and HR zones per week or month and the resulting 3-zone model (polarized / pyramidal / threshold-heavy). Use when the user asks how their training intensity is distributed, about zone 2 vs intensity balance, polarized training, or "grey zone" riding.
---

# Intensity distribution

```bash
python3 -m coach.intensity --weeks 8               # weekly
python3 -m coach.intensity --by month --weeks 6    # monthly
python3 -m coach.intensity --json
```

- Power zones are Coggan % of FTP (Z1 <55, Z2 55-75, Z3 75-90, Z4 90-105, Z5 105-120, Z6 120-150, Z7 >150).
  The percentages are of *pedaling* time. Coasting (0 W) is reported separately, because outdoor rides coast
  10-20%.
- 3-zone: low = Z1-Z2, mid = Z3-Z4, high = Z5+. Model labels: polarized (low >= 75% and high > mid),
  pyramidal (low > mid > high), threshold-heavy (mid >= low).
- HR zones (Strava's) lag power in short efforts, so they understate time at high intensity during intervals.
- Only cycling activities count.

Interpretation: most amateur plans should be pyramidal or polarized, with low >= 70-80% of time.
Mid > 30-35% for several weeks, with stagnating power, points to too much tempo/sweetspot "grey zone".
