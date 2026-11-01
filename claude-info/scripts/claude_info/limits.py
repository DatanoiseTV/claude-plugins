"""Rate-limit pacing.

A window's used percentage alone says little: 40% with four hours left is
fine, 40% after ten minutes is not. Assuming the current average rate holds,
this projects where usage ends up when the window resets and, if that is
over 100%, when the limit is hit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
