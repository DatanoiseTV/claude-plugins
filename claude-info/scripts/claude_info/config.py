"""User configuration.

Defaults live here; any key can be overridden in a JSON file at
$CLAUDE_INFO_CONFIG (default ~/.claude/claude-info.json). Nested dicts are
merged, so an override only needs the keys it changes.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS: dict = {
    # Context bar
    "bar_width": 10,
    "bar_width_narrow": 5,
    "narrow_columns": 80,
    "context_warn_pct": 50,
    "context_crit_pct": 80,

    # Rate limits: colour by where usage is heading, not where it is.
    "limit_warn_pct": 80,
    "limit_crit_pct": 100,
    # Don't project until this share of the window has elapsed; early
    # projections swing wildly on a single large request.
    "pace_min_elapsed": 0.08,
    "windows_s": {"five_hour": 18000, "seven_day": 604800},
    "spend_period_days": {"daily": 1, "weekly": 7, "monthly": 30},

    # Prompt cache
    "cache_countdown_s": 300,
    "cache_expiry_warn_s": 60,
    "cache_hit_warn": 0.70,
    "cache_hit_min_requests": 5,
    "cache_miss_recent_s": 600,

    # Cost
    "burn_min_minutes": 10,
    "model_mix_min_share": 0.03,
    "model_mix_max": 2,

    # Subagents whose transcript is idle this long count as stale, not running.
    "agent_stale_s": 600,
    # Up to this many running agents get individual rates; more show a sum.
    "agents_max_rates": 3,

    # Lifetime totals refresh (background, never blocks the status line)
    "lifetime_refresh_s": 60,
    "lifetime_lock_stale_s": 300,

    # Graphs and animation. The spinner advances with wall time, so it moves
    # whenever the status line is re-rendered (events, refreshInterval).
    "spinner_fps": 1.0,
    "phase_stale_s": 900,
    # Per-request rate sparkline next to t/s in the title; 0 turns it off.
    # Off by default: it changes every request and draws the eye away from
    # the phase indicator.
    "rate_spark_len": 0,
    "context_spark_len": 12,
    "spend_spark_days": 7,
    "limit_gauge_width": 8,
    "cache_drain_width": 5,

    # Layout: "box" (framed grid) or "lines" (two plain lines)
    "layout": "box",
    "right_margin": 4,
    # /claude-info width when not writing to a terminal (shown in a code block)
    "report_width": 100,
    "session_name_max": 28,

    "show": {
        "model_mix": True,
        "git": True,
        "pr": True,
        "session_name": True,
        "cache": True,
        "limits": True,
        "burn_rate": True,
        "lines": True,
        "lifetime": True,
        "duration": True,
    },
}


def config_path() -> Path:
    env = os.environ.get("CLAUDE_INFO_CONFIG")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "claude-info.json"


def cache_dir() -> Path:
    env = os.environ.get("CLAUDE_INFO_CACHE_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "cache" / "claude-info"


def projects_dir() -> Path:
    env = os.environ.get("CLAUDE_INFO_PROJECTS_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "projects"


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with config_path().open("r", encoding="utf-8") as f:
            over = json.load(f)
        if isinstance(over, dict):
            _merge(cfg, over)
    except (OSError, ValueError):
        pass
    return cfg
