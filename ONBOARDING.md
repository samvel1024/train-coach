# Onboarding: your own AI cycling coach

This repo turns Claude Code into a cycling coach that reads your Strava rides, works out FTP / fitness / fatigue,
reviews each session, plans your week and puts workouts on your intervals.icu calendar (which syncs to Garmin,
Wahoo or Zwift).

Setup is guided by Claude itself and takes about 30 minutes, most of it waiting for your ride history to download.

## What you need

| Thing | Why | Cost |
|---|---|---|
| A Claude Pro or Max plan | Runs Claude Code | paid |
| [Claude Code](https://claude.com/claude-code) (desktop app or CLI) | The coach itself | included |
| Strava account with rides that have power and/or HR | Source of all ride data | free |
| [intervals.icu](https://intervals.icu) account | Calendar for workouts and coach notes | free |
| macOS or Linux with Python 3.10+, Node 18+, `uv` | Runs the scripts and integrations | free |

On macOS:

```bash
brew install python node uv git
```

## Get started

1. Clone the repo:

   ```bash
   git clone <repo-url> ~/src/train
   ```

2. Open the `~/src/train` folder in Claude Code (desktop: new session → choose the folder; CLI:
   `cd ~/src/train && claude`). When it asks whether to trust the project's MCP servers (`strava`, `intervals-icu`,
   `weather`), approve them.

3. Say:

   > Set me up as a new athlete.

Claude runs the `onboarding` skill. It checks your tools, walks you through creating your Strava API app and
intervals.icu API key, starts downloading your last year of rides, and while that runs, asks you multiple-choice
questions about your FTP, goals, target event and weekly availability. It writes your settings from your answers,
then gives you your current fitness picture and a first week of training.

You'll handle two secrets yourself: your intervals.icu API key (added to your shell profile in your own terminal) and
your Strava app's client ID/secret (entered when the Strava connector asks for them).

Your rides, goals and keys stay on your machine. They're git-ignored and never end up in the repo.

## Day to day

After each ride: "sync and review today's ride". The coach posts a ⭐ score note in intervals.icu.
On Sundays: "weekly review". Any time: "plan my next week", "how's my fitness?", "what are my power PRs?".
To change goals, just tell the coach.

| Ask about | Skill |
|---|---|
| First-time setup | `onboarding` |
| Refresh data | `sync` |
| FTP, zones, fitness/fatigue/form | `athlete-state`, `training-load` |
| One ride in depth, interval by interval | `analyse-session` |
| What workouts you did over a period | `classify-session` |
| Zone 2 vs intensity balance | `intensity-distribution` |
| Aerobic fitness / HR drift trend | `aerobic-fitness` |
| Power PRs, CP/W', durability | `power-profile` |
| Weekly check-in | `weekly-review` |
| Next week's plan → calendar | `plan-workouts` |

Coaching style and review formats are in `CLAUDE.md`. Edit it if you'd rather have a gentler coach.

## Optional: WHOOP readiness

`noop_bridge/` forwards WHOOP recovery/HRV/sleep (via the NOOP Android app) into intervals.icu wellness for readiness
checks. It needs your own Vercel deployment of `noop_bridge/cloud`. Skip it unless you have WHOOP + Android.

## Troubleshooting

- **Strava / intervals.icu tools missing in Claude:** quit Claude Code fully, reopen it in this folder and approve the
  project's MCP servers.
- **`INTERVALS_API_KEY / INTERVALS_ATHLETE_ID not set`:** the variables aren't in the environment Claude was started
  from. Restart the terminal/app after editing `~/.zshrc`.
- **Strava 401 that won't refresh:** say "reconnect Strava".
- **Tests:** `python3 -m unittest discover tests`.
