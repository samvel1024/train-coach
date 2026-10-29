---
name: classify-session
description: Label cycling sessions by type (endurance, tempo, sweetspot, threshold, vo2max, anaerobic, race, recovery, strength) and list their work intervals. Use when the user asks what workouts/intervals they did over a period, how many threshold/VO2 sessions, or to find sessions of a given type.
---

# Classify sessions

```bash
python3 -m coach.classify --days 35                       # recent sessions
python3 -m coach.classify --since 2026-09-01              # from a date
python3 -m coach.classify --since 2026-01-01 --category threshold
python3 -m coach.classify --days 35 --json
```

How sessions are classified:
- **Work intervals** come from manual laps when the ride has >= 3 laps. A lap is work if it is
  >= 85% FTP, or >= 10 min at >= 78% FTP. Otherwise they are detected in the power stream (30 s power >= 85% FTP for >= 2 min,
  or 10 s power >= 120% FTP for 10-119 s). `[laps]`/`[streams]` shows the source.
- Efforts are grouped into **sets** of similar duration. A set gets one category from its *mean* % FTP:
  >=120% anaerobic (<60 s) / vo2max, 105-120% vo2max, 95-105% threshold, 88-95% sweetspot, 76-88% tempo.
  Stream efforts only count when they repeat (>= 3 of >= 30 s, or >= 2 of >= 5 min, at similar power). This
  keeps climbs and surges from turning an endurance ride into a workout. MTB rides without laps are never
  "structured".
- The primary type is the most intense category with enough work (anaerobic 1 min, vo2max 4 min, threshold
  10 min, sweetspot 15 min, tempo 20 min); the others are listed as secondary. Rides without such work are
  labelled by IF: recovery (<0.60 and <90 min), endurance (<0.82), tempo (<0.90), hard-unstructured.
- Races: Strava race flag or "race" in the name.

For one session's details, use the analyse-session skill.
