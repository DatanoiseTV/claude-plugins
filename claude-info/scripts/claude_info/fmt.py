"""Formatting helpers: colours, units, and visible-width measurement."""

from __future__ import annotations

import os
import re

_COLOR = os.environ.get("NO_COLOR") is None


def _sgr(code: str) -> str:
    return f"\033[{code}m" if _COLOR else ""


RESET = _sgr("0")
BOLD = _sgr("1")
DIM = _sgr("2")
RED = _sgr("31")
GREEN = _sgr("32")
YELLOW = _sgr("33")
BLUE = _sgr("34")
MAGENTA = _sgr("35")
CYAN = _sgr("36")


def paint(text: str, *styles: str) -> str:
    if not text:
        return ""
    style = "".join(styles)
    return f"{style}{text}{RESET}" if style else text


def link(text: str, url: str) -> str:
    """OSC 8 hyperlink; terminals without support show the plain text."""
    if not url:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


_ESC_RE = re.compile(r"\033\[[0-9;]*m|\033\]8;;.*?\033\\")


def visible_len(s: str) -> int:
    return len(_ESC_RE.sub("", s))


def tokens(n: float) -> str:
    """1234 -> 1.2k, 1000000 -> 1M, 340000 -> 340k."""
    n = float(n or 0)
    if n >= 1e6:
        v = n / 1e6
        return f"{v:.0f}M" if v >= 10 or v == int(v) else f"{v:.1f}M"
    if n >= 1e3:
        v = n / 1e3
        return f"{v:.0f}k" if v >= 10 else f"{v:.1f}k"
    return f"{int(n)}"


def money(usd: float) -> str:
    usd = float(usd or 0)
    if usd >= 1000:
        return f"${usd / 1000:.1f}k"
    if usd >= 100:
        return f"${usd:.0f}"
    if usd >= 0.10:
        return f"${usd:.2f}"
    return f"${usd:.3f}" if usd > 0 else "$0"


def duration(seconds: float) -> str:
    """Compact duration: 45s, 4m, 2h10, 3d."""
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h}h{m:02d}" if m else f"{h}h"
    d, h = divmod(s // 3600, 24)
    return f"{d}d{h}h" if h and d < 3 else f"{d}d"


def bar(pct: float, width: int) -> str:
    """Eighth-block bar, so a 10-cell bar still resolves 1.25% steps."""
    pct = max(0.0, min(100.0, float(pct or 0)))
    eighths = round(pct / 100 * width * 8)
    full, part = divmod(eighths, 8)
    partial = " ▏▎▍▌▋▊▉"[part] if part else ""
    filled = "█" * full + partial
    return filled + "·" * (width - len(filled))


def tier(value: float, warn: float, crit: float) -> str:
    if value >= crit:
        return RED
    if value >= warn:
        return YELLOW
    return GREEN


# ── Small graphics ───────────────────────────────────────────────────────────

SPARK = "▁▂▃▄▅▆▇█"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def spark(values, floor_zero: bool = True) -> str:
    """Sparkline. With floor_zero the baseline is 0 (good for spend, rates);
    without it the range is min..max, which shows the shape of slowly
    growing series such as context size."""
    vals = [float(v) for v in values]
    if not vals:
        return ""
    lo = 0.0 if floor_zero else min(vals)
    hi = max(vals)
    if hi <= lo:
        return SPARK[0 if hi <= 0 else 3] * len(vals)
    # With a zero baseline, a zero is "nothing happened", not "a little".
    return "".join("·" if floor_zero and v <= 0 else SPARK[min(7, int((v - lo) / (hi - lo) * 7.999))]
                   for v in vals)


def spinner(now: float, fps: float) -> str:
    """Frame for the current time. The status line is re-rendered on events
    and on its refresh timer, so consecutive renders advance the spinner."""
    return SPINNER[int(now * fps) % len(SPINNER)]


def gauge(used_pct: float, marker_pct, width: int, color: str) -> str:
    """Usage line with a marker for how much of the window has elapsed.
    Filled past the marker means usage is ahead of time."""
    used = max(0.0, min(100.0, float(used_pct)))
    filled = round(used / 100 * width)
    mark = None if marker_pct is None else min(width - 1, int(float(marker_pct) / 100 * width))
    out = []
    for i in range(width):
        if i == mark:
            out.append(paint("┃", BOLD))
        elif i < filled:
            out.append(paint("━", color))
        else:
            out.append(paint("─", DIM))
    return "".join(out)


def drain(frac_left: float, width: int, color: str) -> str:
    """Bar that empties as time runs out."""
    n = max(0, min(width, round(frac_left * width)))
    return paint("▰" * n, color) + paint("▱" * (width - n), DIM)
