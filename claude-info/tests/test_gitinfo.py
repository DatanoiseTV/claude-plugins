import subprocess
import tempfile
import unittest
from pathlib import Path

from _path import add_scripts_dir

add_scripts_dir()

from claude_info import gitinfo  # noqa: E402

SAMPLE = """\
# branch.oid 33026d4f23d0e67d014740cd4a6607aedcbcc93e
# branch.head feature/x
# branch.upstream origin/feature/x
# branch.ab +2 -1
1 M. N... 100644 100644 100644 aaa bbb staged.txt
1 .M N... 100644 100644 100644 aaa bbb modified.txt
1 MM N... 100644 100644 100644 aaa bbb both.txt
u UU N... 100644 100644 100644 100644 a b c conflict.txt
? new.txt
? other.txt
"""


class ParseTest(unittest.TestCase):
    def test_sample(self):
        g = gitinfo.parse_porcelain_v2(SAMPLE)
        self.assertEqual(g.branch, "feature/x")
        self.assertEqual((g.ahead, g.behind), (2, 1))
        self.assertEqual((g.staged, g.modified, g.untracked, g.conflicts), (2, 2, 2, 1))
        self.assertTrue(g.dirty)

    def test_detached(self):
        g = gitinfo.parse_porcelain_v2("# branch.oid abcdef1234567\n# branch.head (detached)\n")
        self.assertTrue(g.detached)
        self.assertEqual(g.branch, "abcdef1")
        self.assertFalse(g.has_upstream)


class RepoTest(unittest.TestCase):
    def test_real_repo_and_merge_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)  # noqa: E731
            run("init", "-q", "-b", "main")
            Path(tmp, "f").write_text("x")
            sub = Path(tmp, "sub")
            sub.mkdir()
            g = gitinfo.read(str(sub))
            self.assertEqual(g.branch, "main")
            self.assertEqual(g.untracked, 1)
            Path(tmp, ".git", "MERGE_HEAD").write_text("0" * 40)
            self.assertEqual(gitinfo.read(tmp).operation, "MERGE")

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(gitinfo.read(tmp))


if __name__ == "__main__":
    unittest.main()
