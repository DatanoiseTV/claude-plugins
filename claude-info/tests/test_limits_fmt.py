import unittest

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import fmt  # noqa: E402
from claude_info import limits  # noqa: E402
from claude_info.limits import pace  # noqa: E402

H = 3600


class PaceTest(unittest.TestCase):
    def test_on_track(self):
        # 2.5h into a 5h window at 40% -> 80% at reset, never full.
        p = pace(40, resets_at=1000 + 2.5 * H, window_s=5 * H, now=1000)
        self.assertAlmostEqual(p.projected, 80)
        self.assertFalse(p.will_hit)

    def test_will_hit(self):
        # 1h into a 5h window at 50% -> full in another hour, reset in 4h.
        p = pace(50, resets_at=4 * H, window_s=5 * H, now=0)
        self.assertAlmostEqual(p.projected, 250)
        self.assertAlmostEqual(p.full_in, H)
        self.assertTrue(p.will_hit)

    def test_too_early_to_project(self):
        p = pace(10, resets_at=5 * H - 60, window_s=5 * H, now=0)
        self.assertIsNone(p.projected)
        self.assertAlmostEqual(p.reset_in, 5 * H - 60)

    def test_no_reset(self):
        p = pace(10, resets_at=None, window_s=5 * H, now=0)
        self.assertIsNone(p.reset_in)

    def test_already_full(self):
        p = pace(100, resets_at=H, window_s=5 * H, now=0)
        self.assertEqual(p.full_in, 0)
        self.assertTrue(p.will_hit)


class RecentRateTest(unittest.TestCase):
    def test_record_and_rate(self):
        hist = []
        for i in range(11):  # 10% over 10 minutes
            hist, _ = limits.record(hist, 1000 + i * 60, 20 + i, keep_s=86400)
        rate = limits.recent_rate(hist, 1600, span_s=1800, min_span_s=300)
        self.assertAlmostEqual(rate, 10 / 600)
        self.assertAlmostEqual(limits.time_left(30, rate), 70 * 60)

    def test_reset_clears_history(self):
        hist = [[0, 80.0], [60, 85.0]]
        hist, _ = limits.record(hist, 120, 2.0, keep_s=86400)
        self.assertEqual(hist, [[120, 2.0]])

    def test_needs_enough_history(self):
        hist = [[0, 10.0], [100, 12.0]]
        self.assertIsNone(limits.recent_rate(hist, 100, span_s=1800, min_span_s=300))

    def test_idle_is_zero_and_unbounded(self):
        hist = [[0, 10.0], [600, 10.0]]
        rate = limits.recent_rate(hist, 600, span_s=1800, min_span_s=300)
        self.assertEqual(rate, 0)
        self.assertIsNone(limits.time_left(10, rate))

    def test_no_duplicate_samples(self):
        hist, added = limits.record([[0, 10.0]], 5, 10.0, keep_s=86400)
        self.assertFalse(added)


class FmtTest(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(fmt.tokens(999), "999")
        self.assertEqual(fmt.tokens(1234), "1.2k")
        self.assertEqual(fmt.tokens(340_000), "340k")
        self.assertEqual(fmt.tokens(1_000_000), "1M")
        self.assertEqual(fmt.tokens(1_500_000), "1.5M")

    def test_money(self):
        self.assertEqual(fmt.money(0), "$0")
        self.assertEqual(fmt.money(0.004), "$0.004")
        self.assertEqual(fmt.money(4.123), "$4.12")
        self.assertEqual(fmt.money(212.4), "$212")
        self.assertEqual(fmt.money(1530), "$1.5k")

    def test_duration(self):
        self.assertEqual(fmt.duration(45), "45s")
        self.assertEqual(fmt.duration(4 * 60 + 5), "4m")
        self.assertEqual(fmt.duration(2 * H + 10 * 60), "2h10")
        self.assertEqual(fmt.duration(3 * H), "3h")
        self.assertEqual(fmt.duration(26 * H), "1d2h")

    def test_bar_width_constant(self):
        for pct in (0, 1, 33.3, 50, 99, 100, 140):
            self.assertEqual(len(fmt.bar(pct, 10)), 10, pct)

    def test_spark(self):
        self.assertEqual(fmt.spark([0, 4, 8]), "·▄█")
        self.assertEqual(fmt.spark([100, 104, 108], floor_zero=False), "▁▄█")
        self.assertEqual(fmt.spark([5, 5]), "██")
        self.assertEqual(fmt.spark([5, 5], floor_zero=False), "▄▄")
        self.assertEqual(fmt.spark([]), "")

    def test_gauge_and_drain(self):
        g = fmt._ESC_RE.sub("", fmt.gauge(50, 25, 8, fmt.GREEN))
        self.assertEqual(g, "━━┃━────")
        self.assertEqual(fmt._ESC_RE.sub("", fmt.drain(0.6, 5, fmt.GREEN)), "▰▰▰▱▱")

    def test_stacked_keeps_small_shares_visible(self):
        import re
        bar = fmt.stacked([(980, fmt.GREEN), (18, fmt.YELLOW), (2, fmt.RED)], 8)
        self.assertEqual(fmt._ESC_RE.sub("", bar), "█" * 8)
        if fmt._COLOR:
            runs = {code: len(cells) for code, cells in re.findall(r"\033\[(\d+)m(█+)", bar)}
            self.assertEqual(runs, {"32": 6, "33": 1, "31": 1})
        self.assertEqual(len(fmt._ESC_RE.sub("", fmt.stacked([(0, fmt.GREEN)], 5))), 5)

    def test_spinner_advances(self):
        self.assertNotEqual(fmt.spinner(0, 1), fmt.spinner(1, 1))

    def test_visible_len_ignores_escapes(self):
        s = fmt.paint("abc", fmt.RED) + fmt.link("#12", "https://x.y/1")
        self.assertEqual(fmt.visible_len(s), 6)


if __name__ == "__main__":
    unittest.main()
