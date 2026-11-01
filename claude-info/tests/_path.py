"""Makes the claude_info package importable from the tests directory."""

import sys
from pathlib import Path


def add_scripts_dir() -> None:
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
