"""Boxed grid layout for the status line.

    ╭─ title left ──────────────────────────── title right ─╮
    │ cell      │ cell          │ cell                      │
    │ cell      │ cell          │ cell                      │
    ╰─ footer ──────────────────────────────────────────────╯

Columns hold one cell per row and are sized to their widest cell, so values
stay in place between refreshes. When the box does not fit, whole columns
are dropped by priority; titles and footer drop their lowest-priority parts.
The last column absorbs any spare width so the box spans the terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import fmt as F

TITLE_SEP = " · "
CELL_SEP = " │ "


@dataclass
class Part:
    """A piece of a title or footer, with a drop priority."""

    text: str
    prio: int


@dataclass
class Column:
    cells: List[str]  # one entry per row; "" for an empty cell
    prio: int
    _width: int = field(default=-1, repr=False)

    @property
    def width(self) -> int:
        if self._width < 0:
            self._width = max((F.visible_len(c) for c in self.cells), default=0)
        return self._width

    @property
    def empty(self) -> bool:
        return not any(self.cells)


def join_parts(parts: List[Part], limit: Optional[int]) -> str:
    """Join parts, dropping the lowest priorities until the text fits."""
    parts = [p for p in parts if p.text]
    while parts and limit is not None and F.visible_len(TITLE_SEP.join(p.text for p in parts)) > limit:
        if len(parts) == 1:
            return ""
        parts.pop(min(range(len(parts)), key=lambda i: parts[i].prio))
    return TITLE_SEP.join(p.text for p in parts)


def fit_titles(left: List[Part], right: List[Part], room: int) -> tuple:
    """Fit both titles into `room` columns, dropping the lowest-priority part
    of either side first, so a less important left part can't push out a
    more important right one."""
    left = [p for p in left if p.text]
    right = [p for p in right if p.text]

    def width() -> int:
        return (F.visible_len(TITLE_SEP.join(p.text for p in left))
                + F.visible_len(TITLE_SEP.join(p.text for p in right)))

    while width() > room and (len(left) + len(right)) > 1:
        side = min((s for s in (left, right) if s), key=lambda s: min(p.prio for p in s))
        side.pop(min(range(len(side)), key=lambda i: side[i].prio))
    return TITLE_SEP.join(p.text for p in left), TITLE_SEP.join(p.text for p in right)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - F.visible_len(s))


def render(left: List[Part], right: List[Part], columns: List[Column],
           footer: List[Part], width: Optional[int]) -> str:
    b = lambda s: F.paint(s, F.DIM, F.CYAN)  # noqa: E731  border colour
    cols = [c for c in columns if not c.empty]
    rows = max((len(c.cells) for c in cols), default=0)
    # Drop rows that are empty across every column.
    keep = [r for r in range(rows) if any(r < len(c.cells) and c.cells[r] for c in cols)]

    def body_width(cs: List[Column]) -> int:
        if not cs:
            return 0
        return sum(c.width for c in cs) + len(CELL_SEP) * (len(cs) - 1) + 4

    if width is not None:
        while len(cols) > 1 and body_width(cols) > width:
            cols.pop(min(range(len(cols)), key=lambda i: cols[i].prio))

    # Natural width of the titles: "╭─ " left " ─ " right " ─╮".
    natural_title = (F.visible_len(join_parts(left, None)) + F.visible_len(join_parts(right, None)) + 9)
    total = width if width is not None else max(body_width(cols), natural_title)
    total = max(total, 10)

    # Top border: left title, filler, right title.
    lt, rt = fit_titles(left, right, total - 9)
    # "╭─ " + lt + " " + fill + " " + rt + " ─╮"  -> 8 fixed columns
    fill = total - 8 - F.visible_len(lt) - F.visible_len(rt)
    if not rt:
        fill += 2
    top = b("╭─ ") + lt + " " + b("─" * max(1, fill)) + (" " + rt + b(" ─╮") if rt else b("─╮"))

    out = [top]
    if cols:
        widths = [c.width for c in cols]
        widths[-1] += max(0, total - body_width(cols))  # last column absorbs the slack
        sep = b(CELL_SEP)
        for r in keep:
            cells = [_pad(c.cells[r] if r < len(c.cells) else "", w) for c, w in zip(cols, widths)]
            out.append(b("│ ") + sep.join(cells) + b(" │"))

    ft = join_parts(footer, total - 7)
    if ft:
        out.append(b("╰─ ") + ft + " " + b("─" * max(1, total - 5 - F.visible_len(ft)) + "╯"))
    else:
        out.append(b("╰" + "─" * (total - 2) + "╯"))
    return "\n".join(out)
