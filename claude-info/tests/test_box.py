import unittest

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import fmt  # noqa: E402
from claude_info import box as _box  # noqa: E402
from claude_info.box import Column, Part  # noqa: E402


class box:  # noqa: N801  strips colour so assertions see the layout only
    @staticmethod
    def render(*a, **k):
        return fmt._ESC_RE.sub("", _box.render(*a, **k))


class BoxTest(unittest.TestCase):
    def test_natural_width(self):
        out = box.render([Part("left", 1)], [Part("right", 1)],
                         [Column(["a", "bb"], 1), Column(["ccc", ""], 1)], [], None).split("\n")
        self.assertEqual(len({len(ln) for ln in out}), 1)
        # The last column absorbs the width the titles need.
        self.assertTrue(out[1].startswith("│ a  │ ccc "))
        self.assertTrue(out[1].endswith(" │"))

    def test_empty_rows_and_columns_vanish(self):
        out = box.render([Part("t", 1)], [], [Column(["x", ""], 1), Column(["", ""], 1)], [], 20).split("\n")
        self.assertEqual(len(out), 3)
        self.assertNotIn("│ x │", out[1])

    def test_title_parts_drop_by_priority(self):
        out = box.render([Part("model", 9), Part("speed", 1)], [Part("project", 5)],
                         [Column(["x"], 1)], [], 26).split("\n")
        self.assertIn("model", out[0])
        self.assertIn("project", out[0])
        self.assertNotIn("speed", out[0])
        self.assertEqual(len(out[0]), 26)

    def test_footer(self):
        out = box.render([Part("t", 1)], [], [Column(["x"], 1)], [Part("+1 −2", 1)], 20).split("\n")
        self.assertTrue(out[-1].startswith("╰─ +1 −2 ─"))
        self.assertEqual(len(out[-1]), 20)


if __name__ == "__main__":
    unittest.main()
