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

    def test_tokens_per_second(self):
        req = lambda ts: json.dumps({"type": "api-request", "timestamp": ts})  # noqa: E731
        self.write(
            req("2026-11-01T10:00:00Z"),
            assistant("a", out=500, ts="2026-11-01T10:00:04Z"),
            assistant("a", out=500, ts="2026-11-01T10:00:10Z"),  # last block at +10s
            req("2026-11-01T10:00:12Z"),
            assistant("b", out=300, ts="2026-11-01T10:00:15Z"),  # still streaming
        )
        s = usage.scan_file(self.path, None, self.prices)
        # (500 + 300) tokens over (10 + 3) seconds
        self.assertAlmostEqual(usage.tokens_per_second(s), 800 / 13)

    def test_tokens_per_second_ignores_tiny_requests(self):
        req = lambda ts: json.dumps({"type": "api-request", "timestamp": ts})  # noqa: E731
        self.write(req("2026-11-01T10:00:00Z"), assistant("a", out=5, ts="2026-11-01T10:00:03Z"))
        s = usage.scan_file(self.path, None, self.prices)
        self.assertIsNone(usage.tokens_per_second(s))

    def test_phase_tracking(self):
        def block(mid, kind, ts, stop="tool_use"):
            return json.dumps({"type": "assistant", "timestamp": ts, "message": {
                "id": mid, "model": "claude-opus-5-5", "stop_reason": stop,
                "content": [{"type": kind}],
                "usage": {"input_tokens": 1, "output_tokens": 1}}})
        req = lambda ts: json.dumps({"type": "api-request", "timestamp": ts})  # noqa: E731
        self.write(req("2026-11-01T10:00:00Z"))
        s = usage.scan_file(self.path, None, self.prices)
        start = usage.epoch("2026-11-01T10:00:00Z")
        self.assertEqual(usage.phase(s, start + 5, 900), ("thinking", 5))
        self.write(block("a", "text", "2026-11-01T10:00:06Z"),
                   block("a", "tool_use", "2026-11-01T10:00:08Z"), mode="a")
        s = usage.scan_file(self.path, s, self.prices)
        self.assertEqual(usage.phase(s, start + 10, 900), ("tools", 2))
        # Stale phases are treated as an interrupted turn.
        self.assertIsNone(usage.phase(s, start + 2000, 900))
        self.write(req("2026-11-01T10:00:20Z"),
                   block("b", "text", "2026-11-01T10:00:25Z", stop="end_turn"), mode="a")
        s = usage.scan_file(self.path, s, self.prices)
        self.assertIsNone(usage.phase(s, start + 30, 900))

    def test_context_history(self):
        self.write(assistant("a", inp=100, cr=900), assistant("b", inp=50, cr=1000))
        s = usage.scan_file(self.path, None, self.prices)
        self.assertEqual(s["ctx_hist"], [1000, 1050])

    def test_activity_and_edited_files(self):
        def tool(mid, name, inp):
            return json.dumps({"type": "assistant", "timestamp": "2026-11-01T10:00:00Z", "message": {
                "id": mid, "model": "claude-opus-5-5", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": name, "input": inp}],
                "usage": {"input_tokens": 1, "output_tokens": 1}}})
        self.write(tool("a", "Edit", {"file_path": "/x/sdma.c"}),
                   tool("b", "Write", {"file_path": "/x/kick.c"}),
                   tool("c", "Edit", {"file_path": "/x/sdma.c"}),
                   tool("d", "Bash", {"command": "cd /very/long/path && make -j8"}))
        s = usage.scan_file(self.path, None, self.prices)
        self.assertEqual(usage.activity(s), "Bash: make -j8")
        self.assertEqual(usage.edited_files(s), ["/x/sdma.c", "/x/kick.c"])
        self.write(tool("e", "SubagentHandback", {"message": "done"}), mode="a")
        s = usage.scan_file(self.path, s, self.prices)
        self.assertTrue(usage.handed_back(s))
        self.assertEqual(usage.activity(s), "Bash: make -j8")

    def test_summarize_tool(self):
        self.assertEqual(usage.summarize_tool("Bash", {"command": "ls", "description": "List files"}),
                         "Bash: List files")
        self.assertEqual(usage.summarize_tool("Read", {"file_path": "/a/b/c.py"}), "Read c.py")
        self.assertEqual(usage.summarize_tool("Grep", {"pattern": "TODO"}), 'Grep "TODO"')
        self.assertEqual(usage.summarize_tool("Weird", {}), "Weird")

    def test_local_day(self):
        self.assertRegex(usage.local_day("2026-11-01T10:00:00.123Z"), r"^2026-11-0[12]$")
        self.assertEqual(usage.local_day("garbage"), "")


if __name__ == "__main__":
    unittest.main()
