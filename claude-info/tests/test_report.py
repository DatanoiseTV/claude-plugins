import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import report  # noqa: E402
from claude_info.pricing import default  # noqa: E402


class DecodeTest(unittest.TestCase):
    def test_encode(self):
        self.assertEqual(report.encode_project("/Users/a/dev/x.y-z"), "-Users-a-dev-x-y-z")

    def test_decode_resolves_ambiguous_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp).resolve() / "dev" / "drum-ng" / "build.v2"
            real.mkdir(parents=True)
            (Path(tmp).resolve() / "dev" / "drum").mkdir()
            self.assertEqual(report.decode_project(report.encode_project(str(real))), real)

    def test_decode_missing_dir(self):
        self.assertIsNone(report.decode_project("-definitely-not-a-real-path-xyz"))


class DataTest(unittest.TestCase):
    def test_cache_savings(self):
        models = {"claude-opus-5-5": {"input": 0, "cw5m": 0, "cw1h": 0, "read": 1_000_000,
                                      "output": 0, "requests": 1, "cost": 0.20}}
        actual, uncached = report.cache_savings(default(), models)
        self.assertAlmostEqual(actual, 0.20)
        self.assertAlmostEqual(uncached, 4.00)

    def test_history_windows(self):
        summary = {
            "total": {"cost": 10.0},
            "days": {"2026-11-01": {"cost": 1.0}, "2026-10-28": {"cost": 2.0},
                     "2026-10-10": {"cost": 3.0}, "2026-08-01": {"cost": 4.0}},
            "models": {}, "projects": {"-x": {"cost": 10.0, "requests": 3, "sessions": 1}},
        }
        h = report.collect_history(summary, default(), today=date(2026, 11, 1))
        self.assertEqual((h["today"], h["week"], h["month"], h["all"]), (1.0, 3.0, 6.0, 10.0))
        self.assertEqual(len(h["trend"]), report.TREND_DAYS)
        self.assertEqual(h["trend"][-1], 1.0)
        self.assertEqual(h["projects"][0]["share"], 1.0)

    def test_spark(self):
        self.assertEqual(report.spark([0, 1, 2]), "·▄█")
        self.assertEqual(report.spark([0, 0]), "··")


class RenderTest(unittest.TestCase):
    def sample(self):
        return {
            "build": "v0", "pricing_date": "2026-11-01", "cwd": "/x",
            "session": {"id": "abcdef12", "project": "~/x", "cost": 1.0, "uncached": 5.0,
                        "main_cost": 0.6, "main_tps": 90.0,
                        "models": [{"name": "Opus 5.5", "requests": 3, "input": 1, "read": 1e6,
                                    "write": 1e4, "output": 1e3, "cost": 1.0, "share": 1.0}],
                        "agents": [{"desc": "worker", "type": "", "model": "Haiku 5.5",
                                    "running": True, "tps": 150.0, "cost": 0.4}]},
            "history": {"today": 1.0, "week": 2.0, "month": 3.0, "all": 4.0, "trend": [0.0] * 13 + [1.0],
                        "active_days": 1, "models": [], "projects": []},
        }

    def test_plain(self):
        out = report.render_plain(self.sample())
        self.assertIn("cache saved $4.00", out)
        self.assertIn("worker", out)

    @unittest.skipUnless(report.rich_available(), "vendor/rich submodule not checked out")
    def test_rich_fits_width(self):
        import io
        import contextlib
        buf = io.StringIO()
        old = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            with contextlib.redirect_stdout(buf):
                report.render_rich(self.sample(), width=80)
        finally:
            if old is None:
                os.environ.pop("NO_COLOR", None)
        lines = buf.getvalue().splitlines()
        self.assertTrue(any("Session abcdef12" in ln for ln in lines))
        self.assertTrue(any("150" in ln and "running" in ln for ln in lines))
        self.assertTrue(all(len(ln) <= 80 for ln in lines))


if __name__ == "__main__":
    unittest.main()
