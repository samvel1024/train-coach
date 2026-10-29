---
name: athlete-state
description: Current athlete snapshot for cycling coaching — FTP set vs estimated from recent efforts, W/kg, power and HR zones, threshold HR, fitness/fatigue/form, last 7 days. Use when the user asks about their FTP, zones, current fitness/form, "how am I doing", or before giving any training advice that depends on FTP or load.
---

# Athlete state

```bash
python3 -m coach.athlete_state          # markdown
python3 -m coach.athlete_state --json   # for further reasoning
```

How to read it:
- **FTP advice** compares the set FTP (Strava profile, overridable per date via `ftp_history` in
  `coach_config.json`) with 95% of the best 20 min and critical power from the last 90 days. Both estimates are
  *lower bounds*: they only see maximal efforts the rider actually did. A lower estimate without any
  hard 20-min effort means "unverified", not "lost fitness".
- **Threshold HR**: the configured value (default = Strava HR zone 4 lower bound) is what hrTSS uses. The
  estimate is the median HR in hard 20-min efforts. If they differ by >5 bpm, suggest setting `lthr` in
  `coach_config.json`.
- **Load**: CTL = fitness (42-day), ATL = fatigue (7-day), TSB = form. Form bands: >5 fresh, -10..5 neutral,
  -30..-10 productive fatigue, < -30 overreached.

If the user confirms a new FTP, add `{"from": "YYYY-MM-DD", "ftp": N}` to `ftp_history` in `coach_config.json`.
Derived metrics recompute automatically for affected dates. Do not change their Strava profile.
