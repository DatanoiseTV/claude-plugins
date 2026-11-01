#!/usr/bin/env python3
"""Status line entry point. Reads the session JSON from stdin."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_info.statusline import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
