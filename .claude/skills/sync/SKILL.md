---
name: sync
description: Pull new Strava activities into the local training dataset and refresh derived metrics. Use before any training analysis when the data may be stale (the user mentions a recent ride that isn't in the data, asks for "latest", or it's a new day), or when the user asks to sync/update/refresh their training data.
---

# Sync training data

Run from the project root:

```bash
python3 backfill.py --days 30 && python3 -m coach.derive
```

- `backfill.py` lists the last 30 days on Strava and downloads only activities missing from `data/raw/`
  (detail, laps, full streams), then rebuilds `data/{activities,laps,streams}.jsonl`.
- `coach.derive` updates `data/derived/activities.jsonl` (NP, TSS, zones, power bests, efforts,
  classification). Every other coach tool also does this automatically.
- For a longer gap use `--days N`. A full year takes ~15 minutes; Strava allows ~100 requests per 15 minutes and the
  script sleeps through limits on its own. For long runs, start it in the background with
  `nohup python3 backfill.py --days 365 > data/backfill.log 2>&1 &`.
- Edited activities (renamed, laps changed) are not re-downloaded automatically: delete
  `data/raw/<id>.json.gz` and rerun.
- Credentials come from the Strava MCP's `~/.config/strava-mcp/config.json`. On a 401 that won't refresh,
  have the user reconnect with the Strava MCP `connect-strava` tool.

Report how many new activities came in (the `[i/N]` log lines) and their dates/names.
