#!/usr/bin/env python3
"""Lifetime token and cost totals across all Claude Code transcripts.

Usage:
    cumulative.py               TSV: cost input cache_write read output requests sessions
    cumulative.py --json        JSON summary (total, projects, days, models)
    cumulative.py --by-project  TSV per project, most expensive first
    cumulative.py --refresh     Refresh the cache quietly (used by the status line)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_info import config, totals  # noqa: E402


def main(argv: list) -> int:
    mode = argv[1] if len(argv) > 1 else "--tsv"
    if mode == "--refresh":
        totals.refresh_locked(config.load())
        return 0

    s = totals.refresh()
    t = s["total"]
    sessions = sum(p["sessions"] for p in s["projects"].values())
    if mode == "--json":
        print(json.dumps(s, indent=2))
    elif mode == "--by-project":
        rows = sorted(s["projects"].items(), key=lambda kv: -kv[1]["cost"])
        for name, p in rows:
            cw = int(p["cw5m"] + p["cw1h"])
            print(f"{p['cost']:.4f}\t{int(p['input'])}\t{cw}\t{int(p['read'])}\t"
                  f"{int(p['output'])}\t{int(p['requests'])}\t{p['sessions']}\t{name}")
    else:
        cw = int(t["cw5m"] + t["cw1h"])
        print(f"{t['cost']:.4f}\t{int(t['input'])}\t{cw}\t{int(t['read'])}\t"
              f"{int(t['output'])}\t{int(t['requests'])}\t{sessions}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
