"""Git state for the status line from a single `git status` call.

`--porcelain=v2 --branch` reports branch, upstream divergence and per-file
state in one pass. In-progress operations (rebase, merge, ...) are detected
from marker files in the git directory, which costs no extra process.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TIMEOUT_S = 1.0

# Marker file or directory in the git dir -> label shown on the status line.
_OPERATIONS = (
    ("rebase-merge", "REBASE"),
    ("rebase-apply", "REBASE"),
    ("MERGE_HEAD", "MERGE"),
    ("CHERRY_PICK_HEAD", "PICK"),
    ("REVERT_HEAD", "REVERT"),
    ("BISECT_LOG", "BISECT"),
)


@dataclass
class GitState:
    branch: str = ""
    detached: bool = False
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = False
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    conflicts: int = 0
    operation: str = ""

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.modified or self.untracked or self.conflicts)


def parse_porcelain_v2(text: str) -> GitState:
    g = GitState()
    oid = ""
    for line in text.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2]
        elif line.startswith("# branch.head "):
            head = line.split(" ", 2)[2]
            if head == "(detached)":
                g.detached = True
            else:
                g.branch = head
        elif line.startswith("# branch.upstream "):
            g.has_upstream = True
        elif line.startswith("# branch.ab "):
            parts = line.split()
            try:
                g.ahead = int(parts[2].lstrip("+"))
                g.behind = int(parts[3].lstrip("-"))
            except (IndexError, ValueError):
                pass
        elif line.startswith(("1 ", "2 ")):
            xy = line[2:4]
            if xy[0] != ".":
                g.staged += 1
            if xy[1] != ".":
                g.modified += 1
        elif line.startswith("u "):
            g.conflicts += 1
        elif line.startswith("? "):
            g.untracked += 1
    if g.detached:
        g.branch = oid[:7] if oid and oid != "(initial)" else "HEAD"
    return g


def find_git_dir(start: Path) -> Optional[Path]:
    """Walk up from `start` to the repository's git directory, following
    `.git` files used by worktrees and submodules."""
    for d in (start, *start.parents):
        dot = d / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():
            try:
                content = dot.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return None
            if content.startswith("gitdir:"):
                p = Path(content[len("gitdir:"):].strip())
                return p if p.is_absolute() else (d / p).resolve()
            return None
    return None


def operation(git_dir: Path) -> str:
    for marker, label in _OPERATIONS:
        if (git_dir / marker).exists():
            return label
    return ""


def read(cwd: str) -> Optional[GitState]:
    if not cwd:
        return None
    start = Path(cwd)
    git_dir = find_git_dir(start)
    if git_dir is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "--no-optional-locks", "status",
             "--porcelain=v2", "--branch", "--untracked-files=normal"],
            capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    g = parse_porcelain_v2(out.stdout)
    g.operation = operation(git_dir)
    return g
