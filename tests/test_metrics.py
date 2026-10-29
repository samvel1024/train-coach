"""Unit tests for coach.metrics on synthetic data."""
import unittest

from coach import metrics as m
from coach import plan_workouts as pw
from coach.analyse_session import blocks, compliance, parse_plan


def steady(watts, seconds):
    return [watts] * seconds


def intervals(reps, work_s, work_w, rest_s, rest_w, warm=600, warm_w=150):
    p = steady(warm_w, warm)
    for _ in range(reps):
        p += steady(work_w, work_s) + steady(rest_w, rest_s)
    return p


class Resample(unittest.TestCase):
    def test_holds_values_and_cuts_long_gaps(self):
        time = [0, 1, 2, 5, 100, 101]
        out, segs, pos = m.resample(time, {"watts": [10, 20, 30, 40, 50, None]})
        # 0,1 -> 1 s each; 2 -> held 3 s; 5 -> gap 95 s > 10 -> 1 s and new segment; then 100, 101
        self.assertEqual(out["watts"], [10, 20, 30, 30, 30, 40, 50, 0])
        self.assertEqual(segs, [(0, 6), (6, 8)])
        self.assertEqual(pos, [0, 1, 2, 5, 6, 7])

    def test_median_sample_interval(self):
        self.assertEqual(m.median_sample_interval([0, 8, 16, 17, 25]), 8)


class PowerMetrics(unittest.TestCase):
    def test_np_of_steady_power_equals_power(self):
        self.assertAlmostEqual(m.normalized_power(steady(250, 3600)), 250)

    def test_np_above_average_for_variable_power(self):
        p = intervals(5, 60, 400, 60, 100, warm=0)
        self.assertGreater(m.normalized_power(p), m.mean(p))

    def test_tss_one_hour_at_ftp_is_100(self):
        self.assertAlmostEqual(m.tss(3600, 300, 300), 100)

    def test_hr_tss(self):
        self.assertAlmostEqual(m.hr_tss(3600, 170, 170), 100)

    def test_zone_seconds(self):
        ftp = 300
        p = steady(100, 10) + steady(250, 20) + steady(400, 5) + [0] * 7
        z = m.zone_seconds(p, m.power_zone_limits(ftp), 7, skip_zero=True)
        self.assertEqual(z, [10, 0, 20, 0, 0, 5, 0])

    def test_mean_max_respects_segments_and_low_res(self):
        p = steady(400, 30) + steady(100, 600)
        self.assertEqual(m.mean_max(p, [(0, len(p))], (5, 30))[30], 400)
        # A window may not span two segments
        self.assertIsNone(m.best_mean(steady(300, 20), 30, [(0, 10), (10, 20)]))
        self.assertNotIn(5, m.mean_max(p, [(0, len(p))], (5, 600), low_res=True))

    def test_cp_fit_recovers_parameters(self):
        cp, wp = 280, 20000
        mmp = {d: cp + wp / d for d in (300, 600, 1200)}
        got_cp, got_wp = m.cp_fit(mmp, (300, 600, 1200))
        self.assertAlmostEqual(got_cp, cp, places=6)
        self.assertAlmostEqual(got_wp, wp, places=3)

    def test_w_prime_balance_depletes_and_recovers(self):
        bal = m.w_prime_balance(steady(400, 100) + steady(100, 600), cp=300, w_prime=20000)
        self.assertAlmostEqual(bal[99], 10000)
        self.assertGreater(bal[-1], 19000)


class Aerobic(unittest.TestCase):
    def test_no_decoupling_when_steady(self):
        self.assertAlmostEqual(m.decoupling(steady(200, 4000), steady(140, 4000)), 0)

    def test_positive_decoupling_when_hr_drifts(self):
        hr = steady(140, 2000) + steady(150, 2000)
        self.assertGreater(m.decoupling(steady(200, 4000), hr), 5)

    def test_decoupling_skipped_for_unsteady_ride(self):
        self.assertIsNone(m.decoupling(steady(250, 2000) + steady(150, 2000), steady(140, 4000)))


class Efforts(unittest.TestCase):
    ftp = 300

    def test_detects_threshold_intervals(self):
        p = intervals(4, 600, 310, 300, 120)
        eff = m.detect_efforts(p, self.ftp)
        self.assertEqual(len(eff), 4)
        for e in eff:
            self.assertAlmostEqual(e["duration"], 600, delta=30)
            self.assertEqual(e["category"], "threshold")
        self.assertTrue(m.label_sets(eff, from_laps=False))
        self.assertEqual(m.classify("Ride", False, 0.8, 4000, eff, True)[0], "threshold")

    def test_random_surges_are_not_a_workout(self):
        p = steady(180, 3000)
        for start in (500, 1700):  # two unrelated 15 s surges
            p[start:start + 15] = [500] * 15
        eff = m.detect_efforts(p, self.ftp)
        self.assertEqual(len(eff), 2)
        self.assertFalse(m.label_sets(eff, from_laps=False))
        self.assertEqual(m.classify("Ride", False, 0.62, 3000, eff, False)[0], "endurance")

    def test_lap_efforts_and_set_category_from_mean(self):
        laps = [{"elapsed_time": 600, "average_watts": 150, "lap_index": 0}]
        # 30/15 micro-intervals: some reps over 120% FTP, set mean ~110% -> vo2max, not anaerobic
        for i, w in enumerate([330, 380, 320, 370, 310, 335] * 3):
            laps.append({"elapsed_time": 31, "average_watts": w, "lap_index": i + 1})
            laps.append({"elapsed_time": 16, "average_watts": 150, "lap_index": i + 100})
        eff = m.lap_efforts(laps, self.ftp)
        self.assertEqual(len(eff), 18)  # 16 s laps are too short to be work
        m.label_sets(eff, from_laps=True)
        self.assertEqual({e["category"] for e in eff}, {"vo2max"})
        self.assertEqual(m.classify("Ride", False, 0.8, 4000, eff, True)[0], "vo2max")

    def test_tempo_lap_counts(self):
        laps = [{"elapsed_time": 900, "average_watts": 150}, {"elapsed_time": 1800, "average_watts": 250},
                {"elapsed_time": 900, "average_watts": 150}]
        self.assertEqual([e["category"] for e in m.lap_efforts(laps, self.ftp)], ["tempo"])

    def test_classify_fallbacks(self):
        self.assertEqual(m.classify("WeightTraining", False, None, 3600, [], False, is_cycling=False)[0], "strength")
        self.assertEqual(m.classify("Ride", True, 0.9, 3600, [], False)[0], "race")
        self.assertEqual(m.classify("Ride", False, 0.55, 2400, [], False)[0], "recovery")
        self.assertEqual(m.classify("Ride", False, 0.95, 3600, [], False)[0], "hard-unstructured")

    def test_summarize(self):
        eff = [{"duration": 600, "avg_power": 320, "pct_ftp": 1.02, "category": "threshold"}] * 4
        self.assertEqual(m.summarize_efforts(eff), "4x10:00 @ 320W (102%)")


class Load(unittest.TestCase):
    def test_pmc_converges_to_constant_load(self):
        ctl = atl = 0
        for _ in range(400):
            ctl, atl = m.pmc_step(ctl, atl, 100)
        self.assertAlmostEqual(ctl, 100, delta=0.1)
        self.assertAlmostEqual(atl, 100, delta=0.001)

    def test_daily_load_for_ramp(self):
        ctl, target = 70, m.daily_load_for_ramp(70, 5)
        for _ in range(7):
            ctl, _ = m.pmc_step(ctl, 0, target)
        self.assertAlmostEqual(ctl, 75, delta=0.01)

    def test_distribution_model(self):
        self.assertEqual(m.distribution_model(0.8, 0.05, 0.15), "polarized")
        self.assertEqual(m.distribution_model(0.65, 0.25, 0.10), "pyramidal")
        self.assertEqual(m.distribution_model(0.40, 0.45, 0.15), "threshold-heavy")


class SessionAnalysis(unittest.TestCase):
    def test_parse_plan(self):
        self.assertEqual(parse_plan("4x10min@320W", 300), [{"reps": 4, "duration": 600, "target_w": 320}])
        self.assertEqual(parse_plan("3x15m @ 95%, 13x30s@120%", 300),
                         [{"reps": 3, "duration": 900, "target_w": 285},
                          {"reps": 13, "duration": 30, "target_w": 360}])
        self.assertEqual(parse_plan("2x20min Tempo", 300)[0]["target_w"], None)
        self.assertEqual(parse_plan("3x 30 15s", 300), [])
        self.assertEqual(parse_plan("Morning Ride", 300), [])

    def test_compliance_counts_cut_short_reps(self):
        eff = [{"duration": d, "avg_power": 310, "set": 0} for d in (914, 926, 675)]
        c = compliance([{"reps": 3, "duration": 900, "target_w": 315}], eff)[0]
        self.assertEqual((c["done_reps"], c["full_reps"], c["verdict"]), (3, 2, "partially completed"))
        c = compliance([{"reps": 2, "duration": 900, "target_w": 300}], eff)[0]
        self.assertEqual(c["verdict"], "completed")

    def test_blocks_split_on_long_recoveries(self):
        reps, t = [], 0
        for block in range(3):
            for _ in range(6):
                reps.append({"start": t, "duration": 30})
                t += 45
            t += 400
        self.assertEqual([len(b) for b in blocks(reps)], [6, 6, 6])


class PlanSyntax(unittest.TestCase):
    def test_workout_text_is_intervals_icu_syntax(self):
        _, b = pw.interval_session("threshold", (4, 600, 300, 97, 102))
        text = pw.workout_text(b)
        self.assertIn("Main threshold 4x\n- 10m 97-102%\n- 5m 50-55%", text)
        self.assertIn("Warmup\n- 15m ramp 50-75%", text)
        # repeat blocks need a blank line before and after
        self.assertIn("\n\nMain threshold 4x", text)
        self.assertIn("55%\n\nCooldown", text)

    def test_blocks_tss(self):
        self.assertAlmostEqual(pw.blocks_tss([("x", 1, [(3600, 100, 100, False)])]), 100)
        self.assertEqual(pw.blocks_seconds([("x", 3, [(60, 100, 100, False), (30, 50, 50, False)])]), 270)

    def test_progression(self):
        last = {"date": "2026-10-04", "work_s": 2460, "pct": 1.02}
        rung, _ = pw.next_rung("threshold", last)
        self.assertGreater(pw.rung_work(rung), 2460)
        under = {"date": "2026-10-04", "work_s": 2400, "pct": 0.90}
        rung, why = pw.next_rung("threshold", under)
        self.assertEqual(pw.rung_work(rung), 2400)
        self.assertIn("repeating", why)


if __name__ == "__main__":
    unittest.main()
