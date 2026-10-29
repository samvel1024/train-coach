---
name: power-profile
description: Power-duration profile — best power from 5 s to 2 h over 42/90/365 days, W/kg, monthly trend, critical power and W', FTP estimates, new personal bests, and durability (power after 1000-3000 kJ of work). Use when the user asks about PRs/peak power, strengths and weaknesses, W/kg, CP, how their power has changed, or fatigue resistance.
---

# Power profile

```bash
python3 -m coach.power_profile
python3 -m coach.power_profile --pr-days 14
python3 -m coach.power_profile --json
```

- Bests use segment-aware rolling averages on 1 Hz power; windows never span a stop longer than 10 s.
  Coarse recordings (4-8 s samples) contribute only durations >= 5 min, because their short peaks are inflated.
- With interval sessions, a longer duration can legitimately show a higher best than a shorter one (e.g. the
  30-min window around 3x10 min beats any 20-min window inside it). This is not a bug.
- CP/W' come from a linear work-time fit of the 5/10/20-min bests over the last 90 days. Like the FTP
  estimates, they are lower bounds when the rider hasn't done maximal efforts at those durations.
- **Durability**: best 5/20-min power after the first 1000/2000/3000 kJ of a ride, as a % of fresh bests.
  Under ~90% at 2000 kJ points to fatigue resistance as a limiter for long road races and gran fondos.
- Indoor (trainer) and outdoor (power meter) power can differ by a few %; check the dates/names of bests before
  comparing.

When discussing strengths, compare W/kg across durations rather than absolute watts, and say which bests are
recent (42d) vs older (365d).
