import json
import tempfile
import unittest
from pathlib import Path

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import usage  # noqa: E402
from claude_info.pricing import default  # noqa: E402


def assistant(mid, model="claude-opus-5-5", inp=10, cc=0, cr=0, out=5,
              ts="2026-11-01T10:00:00Z", speed="standard", h1=None):
    u = {"input_tokens": inp, "cache_creation_input_tokens": cc,
         "cache_read_input_tokens": cr, "output_tokens": out, "speed": speed}
    if h1 is not None:
        u["cache_creation"] = {"ephemeral_5m_input_tokens": cc - h1, "ephemeral_1h_input_tokens": h1}
    return json.dumps({"type": "assistant", "timestamp": ts,
                       "message": {"id": mid, "model": model, "usage": u}})


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "s.jsonl"
        self.prices = default()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *lines, mode="w"):
        with self.path.open(mode) as f:
            for line in lines:
                f.write(line + "\n")

    def test_duplicate_lines_counted_once(self):
        self.write(assistant("a"), assistant("a"), assistant("a"), assistant("b"))
        s = usage.scan_file(self.path, None, self.prices)
        t = usage.total([s])
        self.assertEqual(t["requests"], 2)
        self.assertEqual(t["input"], 20)

    def test_incremental_append(self):
        self.write(assistant("a"))
        s = usage.scan_file(self.path, None, self.prices)
        self.write(assistant("a"), assistant("b", inp=100), mode="a")
        s = usage.scan_file(self.path, s, self.prices)
        t = usage.total([s])
        self.assertEqual(t["requests"], 2)
        self.assertEqual(t["input"], 110)

    def test_partial_line_waits(self):
        self.path.write_text(assistant("a") + "\n" + assistant("b")[:20])
        s = usage.scan_file(self.path, None, self.prices)
        self.assertEqual(usage.total([s])["requests"], 1)
        self.path.write_text(assistant("a") + "\n" + assistant("b") + "\n")
        s = usage.scan_file(self.path, s, self.prices)
        self.assertEqual(usage.total([s])["requests"], 2)

    def test_truncated_file_rescans(self):
        self.write(assistant("a"), assistant("b"))
        s = usage.scan_file(self.path, None, self.prices)
        self.write(assistant("c"))
        s = usage.scan_file(self.path, s, self.prices)
        self.assertEqual(usage.total([s])["requests"], 1)

    def test_mixed_models_priced_separately(self):
        self.write(assistant("a", model="claude-opus-5-5", inp=1_000_000, out=0),
                   assistant("b", model="claude-haiku-5-5", inp=1_000_000, out=0),
                   assistant("c", model="<synthetic>", inp=0, out=0))
        s = usage.scan_file(self.path, None, self.prices)
        m = usage.by_model([s])
        self.assertAlmostEqual(m["claude-opus-5-5"]["cost"], 4.0)
        # One million tokens in a single prompt crosses Haiku's long tier.
        self.assertAlmostEqual(m["claude-haiku-5-5"]["cost"], 0.5)
        self.assertEqual(s["last_model"], "claude-haiku-5-5")

    def test_cache_ttl_split(self):
        self.write(assistant("a", inp=0, cc=1_000_000, h1=1_000_000, out=0))
        s = usage.scan_file(self.path, None, self.prices)
        t = usage.total([s])
        self.assertEqual(t["cw1h"], 1_000_000)
        self.assertAlmostEqual(t["cost"], 8.0)

    def test_fast_mode(self):
        self.write(assistant("a", inp=0, out=1_000_000, speed="fast"))
        s = usage.scan_file(self.path, None, self.prices)
        self.assertAlmostEqual(usage.total([s])["cost"], 40.0)

    def test_session_includes_subagents(self):
        self.write(assistant("a", model="claude-opus-5-5"))
        sub = self.dir / "s" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-1.jsonl").write_text(assistant("x", model="claude-haiku-5-5") + "\n")
        r = usage.scan_session(self.path, self.dir / "cache", "sess-1", self.prices)
        self.assertEqual(set(usage.by_model(r["all"])), {"claude-opus-5-5", "claude-haiku-5-5"})
        self.assertEqual(r["main"]["last_model"], "claude-opus-5-5")
        # Second call reads from the persisted state without rescanning.
        r2 = usage.scan_session(self.path, self.dir / "cache", "sess-1", self.prices)
        self.assertEqual(usage.total(r2["all"])["requests"], 2)

    def test_local_day(self):
        self.assertRegex(usage.local_day("2026-11-01T10:00:00.123Z"), r"^2026-11-0[12]$")
        self.assertEqual(usage.local_day("garbage"), "")


if __name__ == "__main__":
    unittest.main()
