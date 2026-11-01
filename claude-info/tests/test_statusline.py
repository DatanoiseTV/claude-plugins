import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import fmt, statusline  # noqa: E402
from claude_info.statusline import Seg, fit  # noqa: E402


def plain(s: str) -> str:
    return fmt._ESC_RE.sub("", s)


def line(mid, model, out=400, ts="2026-11-01T10:00:10Z", inp=1000):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": model,
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}})


def request(ts):
    return json.dumps({"type": "api-request", "timestamp": ts})


class FitTest(unittest.TestCase):
    def test_drops_lowest_priority_first(self):
        segs = [Seg("aaaa", 100), Seg("bb", 5), Seg("cccc", 50)]
        self.assertEqual(fit(segs, None), "aaaa  bb  cccc")
        self.assertEqual(fit(segs, 10), "aaaa  cccc")
        self.assertEqual(fit(segs, 4), "aaaa")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = {k: os.environ.get(k) for k in ("CLAUDE_INFO_CACHE_DIR", "CLAUDE_INFO_CONFIG", "COLUMNS")}
        os.environ["CLAUDE_INFO_CACHE_DIR"] = str(root / "cache")
        # Segment tests read the plain two-line layout; the box has its own tests.
        (root / "cfg.json").write_text(json.dumps({"layout": "lines"}))
        os.environ["CLAUDE_INFO_CONFIG"] = str(root / "cfg.json")
        os.environ.pop("COLUMNS", None)
        self.proj = root / "projects" / "-tmp-demo"
        self.proj.mkdir(parents=True)
        self.transcript = self.proj / "sess.jsonl"
        self.transcript.write_text("\n".join([
            # A Haiku request, then a switch to Opus (the current model).
            request("2026-11-01T10:00:00Z"),
            line("a", "claude-haiku-5-5", out=400, ts="2026-11-01T10:00:04Z", inp=50_000),
            request("2026-11-01T10:00:20Z"),
            line("b", "claude-opus-5-5", out=800, ts="2026-11-01T10:00:30Z"),
        ]) + "\n")
        self.now = time.time()
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.j = {
            "session_id": "sess",
            "transcript_path": str(self.transcript),
            "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
            "workspace": {"current_dir": str(self.workdir), "project_dir": str(self.workdir)},
            "cost": {"total_cost_usd": 3.0, "total_duration_ms": 3_600_000,
                     "total_lines_added": 10, "total_lines_removed": 2},
            "context_window": {"total_input_tokens": 100_000, "context_window_size": 1_000_000,
                               "used_percentage": 10},
            "prompt_cache": {"warm": True, "caching_observed": True, "ttl": "5m",
                             "expires_at": self.now + 240, "requests": 3, "hit_ratio": 0.9},
            "rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": self.now + 4 * 3600}},
        }

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def render(self, j=None):
        ctx = statusline.build_context(j or self.j, now=self.now)
        ctx.summary = None
        return plain(statusline.render(ctx))

    def render_raw(self, j):
        ctx = statusline.build_context(j, now=self.now)
        ctx.summary = None
        return plain(statusline.render(ctx))

    def test_basic_layout(self):
        out = self.render()
        l1, l2 = out.split("\n")
        self.assertTrue(l1.startswith("Opus 5.5"))
        self.assertIn("work", l1)
        self.assertIn("10% 100k/1M", l2)
        self.assertIn("$3.00", l2)
        self.assertIn("$3.00/h", l2)
        self.assertIn("cache 4m", l2)
        self.assertIn("+10 −2", l2)

    def test_model_mix_and_speed(self):
        l1 = self.render().split("\n")[0]
        # 800 + 400 tokens over 10 + 4 seconds
        self.assertIn(f"{1200 / 14:.0f} t/s", l1)
        self.assertIn("+Haiku 5.5", l1)

    def test_model_switch_warns_about_recache(self):
        j = dict(self.j, model={"id": "claude-sonnet-5-5", "display_name": "Sonnet 5.5"})
        l2 = self.render(j).split("\n")[1]
        # Last request was Opus, now Sonnet: 100k at Sonnet's 5m write rate.
        self.assertIn("new model: next turn re-caches 100k ≈$0.25", l2)

    def test_cold_cache_cost(self):
        j = json.loads(json.dumps(self.j))
        j["prompt_cache"].update(warm=False, recache_tokens_if_cold=100_000)
        l2 = self.render(j).split("\n")[1]
        # 100k at Opus 5.5's 5-minute write rate
        self.assertIn("cache cold: next turn ≈$0.50", l2)

    def test_model_switch_takes_precedence_over_cold(self):
        j = json.loads(json.dumps(self.j))
        j["model"] = {"id": "claude-haiku-5-5"}
        j["prompt_cache"].update(warm=False)
        l1, l2 = self.render(j).split("\n")
        self.assertIn("new model: next turn re-caches 100k ≈$0.013", l2)
        self.assertNotIn("cache cold", l2)
        self.assertTrue(l1.startswith("Haiku 5.5"))
        self.assertIn("+Opus 5.5", l1)

    def test_limit_that_will_run_out(self):
        l2 = self.render().split("\n")[1]
        # 50% after 1h of 5h: full in another hour, before the 4h reset.
        self.assertIn("5h 50% full in 1h ↻4h", l2)

    def test_running_agent(self):
        sub = self.proj / "sess" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-x.jsonl").write_text("\n".join([
            request("2026-11-01T10:00:00Z"),
            line("s1", "claude-haiku-5-5", out=1000, ts="2026-11-01T10:00:05Z"),
        ]) + "\n")
        (sub / "agent-x.meta.json").write_text(json.dumps({"finished": False, "description": "x"}))
        l1 = self.render().split("\n")[0]
        self.assertIn("1 agent 200 t/s", l1)
        (sub / "agent-x.meta.json").write_text(json.dumps({"finished": True}))
        self.assertNotIn("agent", self.render().split("\n")[0])

    def test_narrow_terminal_keeps_essentials(self):
        os.environ["COLUMNS"] = "50"
        l1, l2 = self.render().split("\n")
        self.assertTrue(l1.startswith("Opus 5.5"))
        self.assertIn("10%", l2)
        self.assertLessEqual(len(l2), 50)

    def render_box(self, cols):
        os.environ["COLUMNS"] = str(cols)
        j = json.loads(json.dumps(self.j))
        j["rate_limits"]["seven_day"] = {"used_percentage": 10, "resets_at": self.now + 6 * 86400}
        ctx = statusline.build_context(j, now=self.now)
        ctx.summary = None
        ctx.graphs = True
        return plain(statusline.render_box(ctx, cols - ctx.cfg["right_margin"])).split("\n")

    def test_box_grid(self):
        lines = self.render_box(140)
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("╭─ Opus 5.5"))
        self.assertTrue(lines[-1].startswith("╰─ +Haiku 5.5 21% · +10 −2"))
        self.assertEqual({len(ln) for ln in lines}, {136})
        # Column separators line up across both body rows.
        seps = [[i for i, ch in enumerate(ln) if ch == "│"] for ln in lines[1:3]]
        self.assertEqual(seps[0], seps[1])
        # Gauge: half used, a fifth of the window elapsed -> marker in cell 1.
        self.assertIn("5h ━┃━━──── 50%", lines[1])
        self.assertIn("cache ▰▰▰▰▱ 4m", lines[1])
        self.assertIn("7d ", lines[2])

    def test_box_drops_columns_when_narrow(self):
        lines = self.render_box(50)
        self.assertEqual({len(ln) for ln in lines}, {46})
        self.assertIn("10%", lines[1])
        self.assertNotIn("5h", "".join(lines))

    def test_minimal_input(self):
        out = self.render_raw({})
        self.assertTrue(out.startswith("Claude"))


if __name__ == "__main__":
    unittest.main()
