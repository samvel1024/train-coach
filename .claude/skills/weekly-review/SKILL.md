---
name: weekly-review
description: Coach's weekly training review — volume vs previous 4 weeks, every session with its type, load and form, intensity mix, key-session execution (fade, HR drift, plan compliance), endurance-ride markers, new bests, and rule-based coach notes. Use when the user asks how their week went, for a weekly summary/check-in, or "how's my training going".
---

# Weekly review

1. If the dataset may be missing recent rides, run the sync skill first.
2. Run:

```bash
python3 -m coach.weekly_review            # current week so far (Mon-today)
python3 -m coach.weekly_review --last     # previous full week
python3 -m coach.weekly_review --week 2026-09-30   # week containing that date
python3 -m coach.weekly_review --last --json
```

3. Write the review as a coach would, using the output:
   - One-line verdict: was this a build, a recovery, or an off week, and did it do its job?
   - Volume and load vs the 4-week average, and where form ended up.
   - Key sessions: hit or missed, pacing (fade), physiological response (HR drift).
   - Endurance markers and anything notable (new bests, high decoupling).
   - "Coach notes" from the tool are rule-based flags. Keep the ones that matter, add context, and drop noise.
   - Finish with 2-3 concrete priorities for next week. Offer the plan-workouts skill to turn them into
     sessions.

For deeper looks, use analyse-session (one ride), training-load (multi-week trend), intensity-distribution,
power-profile or aerobic-fitness.
