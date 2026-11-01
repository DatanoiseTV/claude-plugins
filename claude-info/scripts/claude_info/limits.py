"""Rate-limit pacing.

A window's used percentage alone says little: 40% with four hours left is
fine, 40% after ten minutes is not. Assuming the current average rate holds,
this projects where usage ends up when the window resets and, if that is
over 100%, when the limit is hit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Pace:
    used: float                 # percent used now
    reset_in: Optional[float]   # seconds until the window resets
    projected: Optional[float]  # percent at reset at the current rate
    full_in: Optional[float]    # seconds until 100% at the current rate

    @property
    def will_hit(self) -> bool:
        return (
            self.full_in is not None
            and self.reset_in is not None
            and self.full_in < self.reset_in
        )


def pace(used: float, resets_at: Optional[float], window_s: float, now: float,
         min_elapsed: float = 0.08) -> Pace:
    used = max(0.0, float(used))
    if not resets_at or window_s <= 0:
        return Pace(used, None, None, None)

    reset_in = max(0.0, float(resets_at) - now)
    elapsed = window_s - reset_in
    if elapsed < window_s * min_elapsed or elapsed <= 0:
        return Pace(used, reset_in, None, None)

    rate = used / elapsed  # percent per second
    projected = used + rate * reset_in
    if used >= 100:
        full_in: Optional[float] = 0.0
    elif rate > 0:
        full_in = (100.0 - used) / rate
    else:
        full_in = None
    return Pace(used, reset_in, projected, full_in)


# ── Recent consumption ───────────────────────────────────────────────────────
#
# The window average hides bursts: an hour of idling followed by a heavy
# agent run averages out to "fine". Samples of used_percentage collected
# across status line refreshes give the rate over the last few minutes,
# which is what "how long until I hit the limit at this pace" needs.

Sample = List[float]  # [epoch, used_pct]


def record(samples: List[Sample], now: float, used: float, keep_s: float,
           min_gap_s: float = 20.0) -> tuple:
    """Add a sample; returns (samples, changed). A drop in usage means the
    window reset, so older samples no longer apply."""
    samples = [s for s in samples if now - s[0] <= keep_s]
    if samples and used < samples[-1][1] - 0.5:
        samples = []
    if samples and (now - samples[-1][0] < min_gap_s and abs(used - samples[-1][1]) < 1e-9):
        return samples, False
    samples.append([now, used])
    return samples, True


def recent_rate(samples: List[Sample], now: float, span_s: float,
                min_span_s: float) -> Optional[float]:
    """Percent per second over the last `span_s`, or None without enough
    history. Zero means nothing was consumed in that span."""
    pts = [s for s in samples if now - s[0] <= span_s]
    if len(pts) < 2:
        return None
    t0, u0 = pts[0]
    t1, u1 = pts[-1]
    if t1 - t0 < min_span_s:
        return None
    return max(0.0, (u1 - u0) / (t1 - t0))


def time_left(used: float, rate: Optional[float]) -> Optional[float]:
    """Seconds until 100% at `rate` percent per second."""
    if rate is None or rate <= 0:
        return None
    return max(0.0, (100.0 - used) / rate)
