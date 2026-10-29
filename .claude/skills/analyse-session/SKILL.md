---
name: analyse-session
description: Deep dive into a single ride — interval-by-interval power/HR/cadence, fade across reps, HR drift and recovery, W' balance, time in zones, and whether a planned workout was completed. Use when the user asks how a specific workout/ride went, whether they hit their intervals, or to review "today's"/"yesterday's"/"the latest" session.
---

# Analyse a session

```bash
python3 -m coach.analyse_session latest
python3 -m coach.analyse_session --date 2026-10-04
python3 -m coach.analyse_session <strava_activity_id>
python3 -m coach.analyse_session --date 2026-10-02 --plan "3x15min@100%"
python3 -m coach.analyse_session latest --json
```

`--plan` takes comma-separated parts like `4x10min@320W`, `3x15m@95%`, `13x30s@120%` (% = of FTP). Without
`--plan`, the ride name is parsed the same way (e.g. "4x10min"). Run the sync skill first if the ride is newer than
the dataset. `--date` picks the longest activity that day.

Interpreting the output:
- **Fade first->last**: change in power from first to last rep. Within ±3% = well paced. Below -8% = targets
  too high or rests too short.
- **Power CV**: rep-to-rep consistency. Under 3% for long intervals is good. Micro-intervals are naturally higher.
- **HR drift / W/beat change**: rising HR at the same power across reps = accumulating fatigue/heat. Flat
  or falling HR at constant or higher power = the session was within capacity.
- **HR drop 60s**: heart-rate recovery after each rep. Drops that shrink across reps mean fatigue.
- **W' balance**: how close the rider came to empty, using CP/W' from the prior 90 days. Going below 0% means the
  CP/W' estimates are too low (the rider is fitter than the recent bests show). Skipped on coarse recordings.
- **Decoupling** only appears on steady rides (two halves within 15% power). >5% on long endurance rides =
  aerobic durability limiter (or heat/fueling).
- Rides flagged as a *coarse recording* (4-8 s samples, common since Sep 2026) use the head unit's TSS.
  Peaks under 5 min from them are excluded elsewhere.
- Blocks: long sets of micro-intervals are split at long recoveries, so 3 sets of 13x30/15 show as three rows.

Lead with the verdict (did they do what was planned, and how did it feel physiologically), then the
supporting numbers. Don't paste the whole table unless asked.
