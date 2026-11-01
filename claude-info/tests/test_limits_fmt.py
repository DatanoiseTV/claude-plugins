import unittest

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import fmt  # noqa: E402
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

    def test_visible_len_ignores_escapes(self):
        s = fmt.paint("abc", fmt.RED) + fmt.link("#12", "https://x.y/1")
        self.assertEqual(fmt.visible_len(s), 6)


if __name__ == "__main__":
    unittest.main()
