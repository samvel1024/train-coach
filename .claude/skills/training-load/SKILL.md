---
name: training-load
description: Training load over time — daily TSS, fitness (CTL), fatigue (ATL), form (TSB), CTL ramp rate, weekly volume, monotony/strain, and overload flags. Use when the user asks about fitness/fatigue/form, training load, TSS, whether they're overtraining or ready to race, or weekly volume trends.
---

# Training load

```bash
python3 -m coach.training_load --weeks 8
python3 -m coach.training_load --weeks 12 --json
```

- TSS per activity: power-based from 1 Hz streams. Coarse recordings (4-8 s samples) use the head unit's TSS
  from the description, because coarse sampling inflates NP. Rides without power use hrTSS (`lthr`), and
  activities without power or HR use a TSS-per-hour estimate by sport (`fallback_tss_per_hour` in
  `coach_config.json`).
- CTL/ATL = 42/7-day exponential averages. "Form this morning" is TSB before today's training; "after today's
  training" includes it. Weekly rows show TSB at the end of the week.
- Flags: ramp > 8 CTL/week, TSB < -30, monotony > 2 (same load every day), and recovery/low week
  (< 70% of the prior 3 weeks).
- The chart is seeded from the first 28 days of data, so the first weeks of the dataset are approximate. Recent
  values are reliable.

Coaching rules of thumb: sustainable ramp 3-6 CTL/week; TSB -10 to -30 is normal while building;
aim for +5 to +15 on race day; plan a lighter week every 3-4 weeks.
