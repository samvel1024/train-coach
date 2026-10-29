---
name: aerobic-fitness
description: Aerobic fitness markers on endurance rides — efficiency factor (NP per heartbeat) trend and Pa:HR decoupling, indoor and outdoor separately. Use when the user asks whether their base/aerobic fitness is improving, about zone 2 progress, heart-rate drift, or aerobic durability.
---

# Aerobic fitness

```bash
python3 -m coach.aerobic --days 180
python3 -m coach.aerobic --since 2026-06-01 --min-minutes 90
python3 -m coach.aerobic --json
```

- Uses rides classified as endurance, with power and HR, of at least `--min-minutes` (default 60).
- **EF = NP / avg HR**. Rising EF at similar IF = better aerobic fitness. Only compare like with like: indoor
  vs outdoor are trended separately, and heat, altitude, fatigue and caffeine all move HR.
- **Decoupling**: change in power/HR from the first to the second half, after a 10-min warm-up, pedaling only.
  It is only computed when the halves are within 15% power (steady rides). < 5% = good durability. Over 5%
  on rides > 2 h = limiter (or under-fueling/heat).
- The EF trend line is a linear fit per 30 days. Treat it as noisy with fewer than ~8 rides.
- MTB rides have lower EF (stop-start, upper-body work). Keep them in mind when reading monthly averages.
