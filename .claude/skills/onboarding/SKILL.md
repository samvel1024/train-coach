---
name: onboarding
description: First-time setup of the cycling coach for a new athlete — checks tools, connects Strava and intervals.icu, downloads ride history, then interviews the athlete about FTP, goals, event and weekly availability and writes coach_config.json from the answers. Use when the user says they're new, asks to set up / onboard / get started, when coach_config.json or data/raw is missing, or when Strava/intervals.icu aren't connected yet.
---

# Onboarding

Walk a new athlete from a fresh clone to their first plan. Do one stage at a time, check it worked, then move on.
Skip any stage that is already done (check first, don't ask). The athlete never edits files: you ask, you write.

Ask questions with AskUserQuestion only: multiple choice, at most 4 questions per call, 2–4 options each (the
tool adds "Other" for free text, so a typed number like an FTP goes through "Other"). Never ask in a plain-text list.

## 1. Check the machine

```bash
python3 --version; node --version; uvx --version
```

Needs Python 3.10+, Node 18+ and `uv`. If something is missing, give the exact install command
(`brew install python node uv` on macOS) and wait for the athlete to run it.

## 2. intervals.icu

Check `python3 -m coach.icu events` works. If it exits with `INTERVALS_API_KEY / INTERVALS_ATHLETE_ID not set`:

1. Tell the athlete to sign up at https://intervals.icu, connect Strava (Settings → Connections), and open
   Settings → Developer Settings to generate an API key and see their athlete ID (`i123456`).
2. Have them add, in their own terminal (keys never go through chat):
   ```bash
   echo 'export INTERVALS_API_KEY="..."' >> ~/.zshrc; echo 'export INTERVALS_ATHLETE_ID="i..."' >> ~/.zshrc
   ```
   then restart Claude Code (quit the desktop app fully) so the `intervals-icu` MCP server and the scripts see them.
   Alternative when they can't restart now: put both as `KEY=value` lines in `noop_bridge/.env` (git-ignored) —
   the scripts read it, but the MCP server still needs the environment variables.
3. Re-run `python3 -m coach.icu events` to confirm.

## 3. Strava

Check `~/.config/strava-mcp/config.json` exists with an `accessToken`. If not:

1. Have the athlete create their own API app at https://www.strava.com/settings/api — website `http://localhost`,
   **Authorization Callback Domain `localhost`** — and keep the page open for the Client ID and Secret.
2. Call the Strava MCP's `connect-strava` tool and follow what it says (it collects the ID/secret and opens the
   consent page). If the `strava` MCP tools aren't available, the athlete hasn't approved the project's MCP servers:
   tell them to restart Claude Code in this folder and approve `strava`, `intervals-icu` and `weather`.
3. Confirm with `check-strava-connection`.

## 4. Download history

If `data/raw/` is empty or missing, start the year backfill in the background (it takes ~15 minutes because of
Strava's rate limit) and run stage 5 while it downloads:

```bash
mkdir -p data && nohup python3 backfill.py --days 365 > data/backfill.log 2>&1 &
```

Check `tail -3 data/backfill.log` between question rounds. When it finishes, run `python3 -m coach.derive`.

## 5. Interview → coach_config.json

Ask in these rounds. Adapt options to what you already know (e.g. once data is in, offer the estimated FTP).

**Round 1 — the rider**
- "What's your current FTP?" — options: "I know it (type it in Other)", "Estimate it from my rides",
  "I don't train with power".
- "How many hours a week can you ride, on average?" — "3–5 h", "5–8 h", "8–12 h", "12+ h".
- "How many hard sessions a week can you handle?" — "1", "2 (recommended)", "3".
- "Do you know your threshold heart rate (LTHR)?" — "Yes (type it in Other)", "No, estimate it".

**Round 2 — the goal**
- "What are you training for?" — "A specific event", "General fitness / base", "Raise my FTP",
  "Top-end / VO2max".
- If an event: "What's the event and its date?" (Other: name + date), and "What's the target outcome?" —
  "Finish comfortably", "Finish strong / personal best", "Podium / top of age group".
- "Any target FTP or weight?" — "No targets", "Target FTP (type it)", "Target weight (type it)".

**Round 3 — the week**
- "Which days can you ride?" (multiSelect) — present weekdays and weekend as options, e.g. "Mon–Fri evenings",
  "Saturday", "Sunday", "Lunchtime rides".
- "How long is a typical weekday ride?" — "45 min", "60 min", "90 min", "2 h+".
- "How long can your long weekend ride be?" — "2 h", "3 h", "4 h", "5 h+".
- If availability is irregular: "Should I infer availability from your last 8 weeks instead?" — "Yes", "No".

Then write `coach_config.json` (start from `coach_config.example.json` for the shape):

- `ftp_history`: `[{"from": "<today>", "ftp": N}]`. "Estimate it" → after the backfill, run
  `python3 -m coach.athlete_state --json` and use the higher of `ftp_estimates.ftp_from_20min` / `cp`; tell the
  athlete the number and why. Never leave it empty if the Strava profile has no FTP — the tools need one.
- `lthr`: their number, or `null` (then derived from Strava HR zones).
- `goals.focus`: event → `event`; general fitness → `base`; raise FTP → `threshold`; top-end → `vo2max`.
  Beginners (< 5 h/week, no structured training before) → `base` regardless, and say why.
- `goals.event_name`, `goals.event_date` (YYYY-MM-DD), `goals.targets.{ftp, weight_kg, min_weekly_hours, outcome}`.
- `plan.availability_min`: minutes per day, keys `mon`…`sun` (0 = rest day); `null` if they chose to infer it.
- `plan.key_sessions_per_week`: from round 1. Keep `target_ctl_ramp_per_week: 4` and `recovery_week_every: 4`
  unless they're new to training (then ramp 3).

Show a short summary of what you wrote (not the JSON) and ask "Does this look right?" — "Yes", "Change something".

Also check the Strava profile has a weight (`data/athlete.json` → `profile.weight`). If it's missing, ask for it and
have them set it in Strava (Settings → My Profile), then rerun `python3 backfill.py --build-only`.

## 6. First look

Once the backfill finished and the config is written:

1. Run the `athlete-state` skill and give the verdict: FTP, form, what stands out.
2. Offer the first week with the `plan-workouts` skill; push to intervals.icu only after they approve.
3. Close by telling them the routine: "sync" + "review today's ride" after each ride, "weekly review" on Sundays,
   and that they can change goals any time by just telling you.
