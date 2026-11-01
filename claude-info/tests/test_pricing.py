import unittest

from _path import add_scripts_dir

add_scripts_dir()

from claude_info.pricing import Pricing, Usage, default  # noqa: E402


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.p = default()

    def test_exact_ids(self):
        self.assertEqual(self.p.resolve("claude-opus-5-5"), "claude-opus-5-5")
        self.assertEqual(self.p.resolve("claude-fable-5-1"), "claude-fable-5-1")
        self.assertEqual(self.p.resolve("claude-haiku-5-5"), "claude-haiku-5-5")

    def test_longest_prefix_wins(self):
        self.assertEqual(self.p.resolve("claude-opus-4-5"), "claude-opus-4-5")
        self.assertEqual(self.p.resolve("claude-opus-4"), "claude-opus-4")
        self.assertEqual(self.p.resolve("claude-fable-5"), "claude-fable-5")

    def test_date_and_context_suffixes(self):
        self.assertEqual(self.p.resolve("claude-sonnet-4-5-20250929"), "claude-sonnet-4-5")
        self.assertEqual(self.p.resolve("claude-opus-4-6[1m]"), "claude-opus-4-6")
        self.assertEqual(self.p.resolve("anthropic.claude-opus-5-5"), "claude-opus-5-5")

    def test_unknown_falls_back_to_family(self):
        self.assertEqual(self.p.resolve("claude-opus-9-9"), "claude-opus-5-5")

    def test_synthetic_is_free(self):
        self.assertIsNone(self.p.resolve("<synthetic>"))
        self.assertEqual(self.p.cost("<synthetic>", Usage(input=1000)), 0.0)


class CostTest(unittest.TestCase):
    def setUp(self):
        self.p = default()

    def test_opus_5_5_list_price(self):
        u = Usage(input=1_000_000, output=1_000_000)
        self.assertAlmostEqual(self.p.cost("claude-opus-5-5", u), 24.0)

    def test_cache_categories(self):
        u = Usage(cw5m=1_000_000, cw1h=1_000_000, read=1_000_000)
        # 5.00 + 8.00 + 0.20
        self.assertAlmostEqual(self.p.cost("claude-opus-5-5", u), 13.20)

    def test_haiku_long_prompt_tier(self):
        short = Usage(input=100_000, output=1_000_000)
        long_ = Usage(input=100_001, output=1_000_000)
        self.assertAlmostEqual(self.p.cost("claude-haiku-5-5", short), 0.01 + 0.50)
        self.assertAlmostEqual(self.p.cost("claude-haiku-5-5", long_), 100_001 * 0.50 / 1e6 + 2.50)

    def test_fast_mode_scales_cache_too(self):
        u = Usage(input=1_000_000, read=1_000_000, output=1_000_000, fast=True)
        # input 8, read 0.20 * 2 = 0.40, output 40
        self.assertAlmostEqual(self.p.cost("claude-opus-5-5", u), 48.40)

    def test_fast_ignored_where_unsupported(self):
        u = Usage(input=1_000_000, fast=True)
        self.assertAlmostEqual(self.p.cost("claude-sonnet-5-5", u), 2.0)

    def test_geo_us_multiplier(self):
        u = Usage(input=1_000_000, geo_us=True)
        self.assertAlmostEqual(self.p.cost("claude-opus-5-5", u), 4.4)
        # Not applied to models older than 4.6.
        self.assertAlmostEqual(self.p.cost("claude-opus-4-5", u), 5.0)


class TableSanityTest(unittest.TestCase):
    def test_every_model_has_all_rates(self):
        p = default()
        for key, m in p._models.items():
            for field in ("input", "cw5m", "cw1h", "read", "output", "context"):
                self.assertIn(field, m, f"{key} missing {field}")

    def test_custom_table(self):
        p = Pricing({"models": {"x-model": {"input": 1, "cw5m": 1, "cw1h": 1, "read": 1, "output": 1}}})
        self.assertAlmostEqual(p.cost("x-model", Usage(input=2_000_000)), 2.0)


if __name__ == "__main__":
    unittest.main()
