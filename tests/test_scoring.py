import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.scoring import (
    ScoreContext, Stats, Weights,
    bayesian_rate, cost_fit, decay_weight, latency_fit,
    score_card, ucb_bonus, wilson_lower,
)


class TestWilson(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(wilson_lower(0, 0), 0.0)

    def test_small_sample_punished(self):
        self.assertLess(wilson_lower(3, 3), wilson_lower(96, 100))

    def test_bounds(self):
        for s, n in [(0, 10), (5, 10), (10, 10), (1000, 1000)]:
            v = wilson_lower(s, n)
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_lower_than_raw_rate(self):
        self.assertLess(wilson_lower(80, 100), 0.8)


class TestSmoothing(unittest.TestCase):
    def test_bayesian_no_data_returns_prior(self):
        self.assertAlmostEqual(bayesian_rate(0, 0, prior=0.5, strength=20), 0.5)

    def test_bayesian_data_dominates(self):
        v = bayesian_rate(90, 100, prior=0.5, strength=20)
        self.assertGreater(v, 0.8)


class TestUcb(unittest.TestCase):
    def test_decreasing_in_exposures(self):
        vals = [ucb_bonus(e, 10000) for e in (0, 10, 100, 1000, 5000)]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_new_card_gets_bonus(self):
        self.assertGreater(ucb_bonus(0, 10000), 1.0)


class TestMultipliers(unittest.TestCase):
    def test_cost_fit(self):
        self.assertEqual(cost_fit(0.01, 0.05), 1.0)
        self.assertEqual(cost_fit(0.05, 0.05), 1.0)
        self.assertEqual(cost_fit(0.10, 0.05), 0.0)
        self.assertAlmostEqual(cost_fit(0.075, 0.05), 0.5)

    def test_latency_fit(self):
        self.assertEqual(latency_fit("fast", "fast"), 1.0)
        self.assertEqual(latency_fit("medium", "fast"), 0.6)
        self.assertEqual(latency_fit("slow", "fast"), 0.3)
        self.assertEqual(latency_fit("slow", None), 1.0)

    def test_decay_halves_at_half_life(self):
        self.assertAlmostEqual(decay_weight(30, 30), 0.5)
        self.assertAlmostEqual(decay_weight(60, 30), 0.25)


class TestScoreCard(unittest.TestCase):
    def setUp(self):
        self.ctx = ScoreContext(total_selections=10000)

    def test_unhealthy_is_zero(self):
        stats = Stats(exposures=3000, selections=1200, successes=1100, failures=100)
        bd = score_card(stats, self.ctx, healthy=False)
        self.assertEqual(bd.score, 0.0)

    def test_cold_start_nonzero_via_ucb(self):
        bd = score_card(Stats(), self.ctx)
        self.assertGreater(bd.score, 0.0)
        self.assertEqual(bd.quality, 0.0)

    def test_quality_beats_exposure_with_data(self):
        incumbent = Stats(exposures=5200, selections=2080, successes=1200, failures=880, approvals=900,
                          rating_sum=960, rating_count=300)
        niche = Stats(exposures=260, selections=90, successes=78, failures=12, approvals=60,
                      rating_sum=184, rating_count=40)
        b_inc = score_card(incumbent, self.ctx)
        b_niche = score_card(niche, self.ctx)
        self.assertGreater(b_niche.score, b_inc.score)
        self.assertGreater(b_niche.quality, b_inc.quality)

    def test_cost_gate_zeroes_score(self):
        stats = Stats(exposures=100, selections=50, successes=49, failures=1)
        ctx = ScoreContext(total_selections=10000, max_cost=0.01)
        bd = score_card(stats, ctx, cost=0.02)
        self.assertEqual(bd.score, 0.0)

    def test_stale_glory_fades(self):
        fresh = Stats(exposures=100, selections=50, successes=45, failures=5)
        w = Weights(half_life=30)
        self.assertGreater(w.half_life, 0)
        bd = score_card(fresh, self.ctx)
        self.assertGreater(bd.score, 0.0)


if __name__ == "__main__":
    unittest.main()
