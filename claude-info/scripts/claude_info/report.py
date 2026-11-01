"""Detailed usage report for the /claude-info command.

Costs are list-price estimates from pricing.json. For subscription plans
they express usage in API terms, not what is billed.
"""

from __future__ import annotations

import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__, config, pricing, totals, usage
from . import fmt as F

SPARK = "▁▂▃▄▅▆▇█"


def build_id() -> str:
    """Version plus branch and commit of the plugin checkout, when it is one."""
    root = Path(__file__).resolve().parents[2]
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, timeout=1.0, check=True).stdout.strip()
    try:
        return f"v{__version__} ({git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')})"
    except (OSError, subprocess.SubprocessError):
        return f"v{__version__}"


def encode_project(path: str) -> str:
    """Claude Code's project directory naming: non-alphanumerics -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def find_session(cwd: str, projects: Path) -> Optional[Path]:
    """Most recently written main transcript for the working directory."""
    d = projects / encode_project(cwd)
    candidates = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
    return candidates[0] if candidates else None


def table(rows: List[List[str]], align: str) -> List[str]:
    """Plain text table; `align` holds one 'l' or 'r' per column."""
    if not rows:
        return []
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for r in rows:
        cells = [c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(r, widths, align)]
        out.append("  " + "  ".join(cells).rstrip())
    return out


def cache_savings(prices: pricing.Pricing, models: Dict[str, dict]) -> tuple:
    """(actual cost, cost had every cached token been sent as plain input)."""
    actual = uncached = 0.0
    for mid, t in models.items():
        rates = prices.rates(mid)
        if rates is None:
            continue
        actual += t["cost"]
        extra = (t["read"] + t["cw5m"] + t["cw1h"]) * rates.input / 1e6
        cached_part = (t["read"] * rates.read + t["cw5m"] * rates.cw5m + t["cw1h"] * rates.cw1h) / 1e6
        uncached += t["cost"] - cached_part + extra
    return actual, uncached


def model_rows(prices: pricing.Pricing, models: Dict[str, dict]) -> List[List[str]]:
    rows = [["model", "requests", "input", "cache read", "cache write", "output", "cost"]]
    for mid, t in sorted(models.items(), key=lambda kv: -kv[1]["cost"]):
        if not t["requests"] or mid.startswith("<"):
            continue
        info = prices.info(mid)
        name = info["name"] if info else mid
        rows.append([name, f"{int(t['requests'])}", F.tokens(t["input"]), F.tokens(t["read"]),
                     F.tokens(t["cw5m"] + t["cw1h"]), F.tokens(t["output"]), F.money(t["cost"])])
    return rows


def session_section(transcript: Path, prices: pricing.Pricing) -> List[str]:
    res = usage.scan_session(transcript, config.cache_dir(), transcript.stem, prices)
    files: Dict[str, dict] = res["files"]
    models = usage.by_model(res["all"])
    tot = usage.total(res["all"])
    lines = [f"Session {transcript.stem[:8]}  ({transcript.parent.name})", ""]
    lines += table(model_rows(prices, models), "lrrrrrr")

    actual, uncached = cache_savings(prices, models)
    if uncached > actual > 0:
        lines.append("")
        lines.append(f"  Prompt cache saved {F.money(uncached - actual)} "
                     f"({(1 - actual / uncached) * 100:.0f}% of {F.money(uncached)} uncached)")

    main_tps = usage.tokens_per_second(res["main"])
    agents = []
    for path, st in files.items():
        if st is res["main"]:
            continue
        meta = usage.load_json(Path(path[: -len(".jsonl")] + ".meta.json")) or {}
        t = usage.total([st])
        last = st.get("last_model", "")
        info = prices.info(last)
        agents.append([
            (meta.get("description") or Path(path).stem)[:40],
            info["name"] if info else (last or "-"),
            "done" if meta.get("finished") else "running",
            f"{usage.tokens_per_second(st) or 0:.0f}",
            F.money(t["cost"]),
        ])
    lines.append("")
    main_cost = usage.total([res["main"]])["cost"]
    lines.append(f"  Main thread  {F.money(main_cost)}"
                 + (f"  {main_tps:.0f} tok/s" if main_tps else ""))
    if agents:
        lines.append(f"  Subagents    {F.money(tot['cost'] - main_cost)} across {len(agents)}")
        lines.append("")
        lines += table([["agent", "model", "state", "tok/s", "cost"]] + agents, "lllrr")
    return lines


def spark(values: List[float]) -> str:
    top = max(values) if values else 0
    if top <= 0:
        return "·" * len(values)
    return "".join(SPARK[min(7, int(v / top * 7.999))] if v > 0 else "·" for v in values)


def history_section(summary: dict, prices: pricing.Pricing) -> List[str]:
    days: Dict[str, dict] = summary.get("days") or {}
    today = date.today()

    def window(n: int) -> float:
        start = today - timedelta(days=n - 1)
        return sum(v["cost"] for k, v in days.items() if k and k >= start.isoformat())

    last14 = [days.get((today - timedelta(days=i)).isoformat(), {}).get("cost", 0.0)
              for i in range(13, -1, -1)]
    total = summary.get("total") or {}
    lines = ["All projects", ""]
    lines += table([
        ["today", "7 days", "30 days", "all time"],
        [F.money(window(1)), F.money(window(7)), F.money(window(30)), F.money(total.get("cost", 0))],
    ], "rrrr")
    lines.append("")
    lines.append(f"  last 14 days  {spark(last14)}  peak {F.money(max(last14))}/day")

    lines.append("")
    lines += table(model_rows(prices, summary.get("models") or {}), "lrrrrrr")

    projects = sorted((summary.get("projects") or {}).items(), key=lambda kv: -kv[1]["cost"])[:8]
    if projects:
        lines.append("")
        rows = [["project", "sessions", "requests", "cost"]]
        for name, p in projects:
            rows.append([name.lstrip("-")[-40:], str(p.get("sessions", 0)),
                         str(int(p["requests"])), F.money(p["cost"])])
        lines += table(rows, "lrrr")
    return lines


def render(cwd: str) -> str:
    prices = pricing.default()
    out = [f"claude-info {build_id()}  pricing {pricing_date()}", ""]
    transcript = find_session(cwd, config.projects_dir())
    if transcript:
        out += session_section(transcript, prices)
    else:
        out.append(f"No session transcript found for {cwd}")
    out.append("")
    out += history_section(totals.refresh(), prices)
    out.append("")
    out.append("Costs are list-price estimates; on a subscription they measure usage, not billing.")
    return "\n".join(out)


def pricing_date() -> str:
    data = usage.load_json(pricing.PRICING_FILE) or {}
    return data.get("_retrieved", "unknown")


def main(cwd: Optional[str] = None) -> int:
    import os
    start = time.time()
    print(render(cwd or os.getcwd()))
    if os.environ.get("CLAUDE_INFO_DEBUG"):
        print(f"\n({time.time() - start:.2f}s)")
    return 0
