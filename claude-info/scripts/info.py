#!/usr/bin/env python3
"""Detailed usage report, used by the /claude-info command."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_info.report import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
