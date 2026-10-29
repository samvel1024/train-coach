---
name: plan-workouts
description: Plan the next week of cycling training from current fitness/fatigue, recent key sessions and goals, and optionally push the workouts to the intervals.icu calendar (which syncs to Garmin/Wahoo/Zwift). Use when the user asks what to ride next, for a training plan or next week's schedule, or to put workouts on their calendar.
---

# Plan workouts

1. Check goals and availability in `coach_config.json` (`goals.focus`: base | sweetspot | threshold | vo2max |
   event, with `goals.event_date`; `plan.availability_min` per weekday, e.g.
   `{"mon": 0, "tue": 90, ...}`). If availability is null, it is inferred from the last 8 weeks. If the user
   states goals or availability, update the config file rather than passing one-off flags.
2. Generate the plan:

```bash
python3 -m coach.plan_workouts                       # 7 days from tomorrow
python3 -m coach.plan_workouts --start 2026-10-12 --days 7 --focus vo2max
python3 -m coach.plan_workouts --json                # includes icu_events
```

How the plan is built:
- **Week type**: recovery if form after yesterday is below -25, or after `recovery_week_every - 1` build weeks.
  Recovery weeks get about 60% of CTL x 7 TSS, one short key session, and endurance rides of at most 90 min.
  Build weeks target the TSS that raises CTL by `target_ctl_ramp_per_week`.
- **Key sessions** (`key_sessions_per_week`, non-consecutive days, not the longest day) rotate through the
  focus categories. Each progresses from the last session of that type in the past 60 days. If that session
  was at target intensity, add work time (next rung of the ladder). If not, repeat the same volume.
- The rest of the TSS budget goes to endurance rides in proportion to availability. Leftover budget extends
  key days with endurance riding.
- TSS estimates are from the step targets (slight underestimate for intervals).

3. Present the plan (table plus key-session structure) and explain the reasoning: week type, focus, progression.
   Adjust it to anything the user says (travel, fatigue, group rides, races). Edit `coach_config.json` and
   rerun, rather than hand-editing workouts.
4. **Only after the user approves**, push to intervals.icu:
   - First check intervals.icu's cycling FTP (`mcp__intervals-icu__icu_get_sport_settings`). Step targets are
     % of *intervals.icu's* FTP. If it differs from the plan's FTP, tell the user before pushing.
   - Check the calendar for existing workouts on those dates (`mcp__intervals-icu__icu_get_calendar_events`)
     and ask before adding duplicates.
   - Call `mcp__intervals-icu__icu_bulk_create_events` with `events` = the `icu_events` array from `--json`,
     serialized as a JSON string. The descriptions are already intervals.icu workout syntax.
   - Report what was created (dates and names).
