"""Usage report for the /claude-info command.

Data collection (`collect`) is separate from presentation. The report is
drawn with Rich (vendored as a git submodule under vendor/rich); without it
a plain text layout is used.

Costs are list-price estimates from pricing.json. For subscription plans
they express usage in API terms, not what is billed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__, config, pricing, totals, usage
from . import fmt as F

VENDOR_RICH = Path(__file__).resolve().parents[2] / "vendor" / "rich"
SPARK = "▁▂▃▄▅▆▇█"
TREND_DAYS = 14
TOP_PROJECTS = 8


# ── Data ─────────────────────────────────────────────────────────────────────

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


def decode_project(encoded: str) -> Optional[Path]:
    """Recover the real directory behind an encoded project name.

    The encoding maps '/', '.', '-' and other characters all to '-', so it
    cannot be reversed by string operations alone. Walking the filesystem
    and taking the longest existing name at each level resolves it for any
    directory that still exists.
    """
    tokens = encoded.lstrip("-").split("-")
    cur = Path("/")
    i = 0
    while i < len(tokens):
        try:
            entries = {encode_project(e.name): e.name for e in os.scandir(cur) if e.is_dir()}
        except OSError:
            return None
        for j in range(len(tokens), i, -1):
            name = entries.get("-".join(tokens[i:j]))
            if name is not None:
                cur = cur / name
                i = j
                break
        else:
            return None
    return cur


def project_label(encoded: str) -> str:
    """Readable project path, home-relative where possible."""
    real = decode_project(encoded)
    home = Path.home()
    if real is not None:
        try:
            return "~/" + str(real.relative_to(home)) if real != home else "~"
        except ValueError:
            return str(real)
    home_enc = encode_project(str(home))
    if encoded.startswith(home_enc + "-"):
        return "~/" + encoded[len(home_enc) + 1:]
    return encoded.lstrip("-")


def find_session(cwd: str, projects: Path) -> Optional[Path]:
    """Most recently written main transcript for the working directory."""
    d = projects / encode_project(cwd)
    if not d.is_dir():
        return None
    candidates = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def cache_savings(prices: pricing.Pricing, models: Dict[str, dict]) -> tuple:
    """(actual cost, cost had every cached token been sent as plain input)."""
    actual = uncached = 0.0
    for mid, t in models.items():
        rates = prices.rates(mid)
        if rates is None:
            continue
        cached_part = (t["read"] * rates.read + t["cw5m"] * rates.cw5m + t["cw1h"] * rates.cw1h) / 1e6
        as_input = (t["read"] + t["cw5m"] + t["cw1h"]) * rates.input / 1e6
        actual += t["cost"]
        uncached += t["cost"] - cached_part + as_input
    return actual, uncached


def model_rows(prices: pricing.Pricing, models: Dict[str, dict]) -> List[dict]:
    rows = []
    total = sum(t["cost"] for t in models.values()) or 1.0
    for mid, t in sorted(models.items(), key=lambda kv: -kv[1]["cost"]):
        if not t["requests"] or mid.startswith("<"):
            continue
        info = prices.info(mid)
        rows.append({
            "name": info["name"] if info else mid,
            "requests": int(t["requests"]),
            "input": t["input"], "read": t["read"], "write": t["cw5m"] + t["cw1h"],
            "output": t["output"], "cost": t["cost"], "share": t["cost"] / total,
        })
    return rows


def collect_session(transcript: Path, prices: pricing.Pricing) -> dict:
    cfg = config.load()
    res = usage.scan_session(transcript, config.cache_dir(), transcript.stem, prices)
    models = usage.by_model(res["all"])
    actual, uncached = cache_savings(prices, models)
    main_cost = usage.total([res["main"]])["cost"]

    agents = []
    for path, st in res["files"].items():
        if st is res["main"]:
            continue
        meta = usage.load_json(Path(path[: -len(".jsonl")] + ".meta.json")) or {}
        info = prices.info(st.get("last_model", ""))
        stale = time.time() - st.get("mtime", 0) > cfg["agent_stale_s"]
        agents.append({
            "desc": meta.get("description") or Path(path).stem,
            "type": meta.get("agentType", ""),
            "model": info["name"] if info else (st.get("last_model") or "-"),
            "running": not meta.get("finished", False) and not stale,
            "tps": usage.tokens_per_second(st),
            "cost": usage.total([st])["cost"],
        })
    agents.sort(key=lambda a: (not a["running"], -a["cost"]))

    return {
        "id": transcript.stem,
        "project": project_label(transcript.parent.name),
        "models": model_rows(prices, models),
        "cost": actual,
        "uncached": uncached,
        "main_cost": main_cost,
        "main_tps": usage.tokens_per_second(res["main"]),
        "agents": agents,
    }


def collect_history(summary: dict, prices: pricing.Pricing, today: Optional[date] = None) -> dict:
    days: Dict[str, dict] = summary.get("days") or {}
    today = today or date.today()

    def window(n: int) -> float:
        start = (today - timedelta(days=n - 1)).isoformat()
        return sum(v["cost"] for k, v in days.items() if k and k >= start)

    trend = [days.get((today - timedelta(days=i)).isoformat(), {}).get("cost", 0.0)
             for i in range(TREND_DAYS - 1, -1, -1)]
    projects = sorted((summary.get("projects") or {}).items(), key=lambda kv: -kv[1]["cost"])
    proj_total = sum(p["cost"] for _, p in projects) or 1.0
    return {
        "today": window(1), "week": window(7), "month": window(30),
        "all": (summary.get("total") or {}).get("cost", 0.0),
        "trend": trend,
        "active_days": sum(1 for v in days.values() if v.get("cost")),
        "models": model_rows(prices, summary.get("models") or {}),
        "projects": [{"name": project_label(n), "sessions": p.get("sessions", 0),
                      "requests": int(p["requests"]), "cost": p["cost"],
                      "share": p["cost"] / proj_total}
                     for n, p in projects[:TOP_PROJECTS]],
    }


def collect(cwd: str) -> dict:
    prices = pricing.default()
    transcript = find_session(cwd, config.projects_dir())
    data = usage.load_json(pricing.PRICING_FILE) or {}
    return {
        "build": build_id(),
        "pricing_date": data.get("_retrieved", "unknown"),
        "cwd": cwd,
        "session": collect_session(transcript, prices) if transcript else None,
        "history": collect_history(totals.refresh(), prices),
    }


def spark(values: List[float]) -> str:
    top = max(values) if values else 0
    if top <= 0:
        return "·" * len(values)
    return "".join(SPARK[min(7, int(v / top * 7.999))] if v > 0 else "·" for v in values)


NOTE = "List-price estimates; on a subscription they measure usage, not billing."


# ── Rich rendering ───────────────────────────────────────────────────────────

def render_rich(d: dict, width: Optional[int] = None) -> None:
    if str(VENDOR_RICH) not in sys.path:
        sys.path.insert(0, str(VENDOR_RICH))
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    tty = sys.stdout.isatty()
    cfg = config.load()
    if width is None:
        try:
            width = int(os.environ["COLUMNS"])
        except (KeyError, ValueError):
            width = None if tty else cfg["report_width"]
    console = Console(width=width,
                      highlight=False, emoji=False,
                      no_color=bool(os.environ.get("NO_COLOR")) or not tty)
    W = console.width

    def bar(frac: float, n: int, style: str = "cyan") -> Text:
        s = F.bar(frac * 100, n)
        filled = s.rstrip("·")
        return Text(filled, style=style) + Text(s[len(filled):], style="grey37")

    def panel(body, title: str, subtitle: str = "") -> Panel:
        return Panel(body, title=f"[bold]{title}[/]", title_align="left",
                     subtitle=subtitle or None, subtitle_align="right",
                     box=box.ROUNDED, border_style="cyan", padding=(0, 1))

    def models_table(rows: List[dict], bar_w: int) -> Table:
        t = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True,
                  header_style="dim")
        # Uncached input is near zero once caching works, so it is the
        # first column to go on a narrow terminal.
        wide = W >= 100
        t.add_column("Model", style="bold", no_wrap=True)
        for col in ("Req",) + (("Input",) if wide else ()) + ("Cache read", "Cache write", "Output"):
            t.add_column(col, justify="right", no_wrap=True)
        t.add_column("Cost", justify="right", style="green", no_wrap=True)
        t.add_column("Share", justify="left", no_wrap=True)
        for r in rows:
            cells = [r["name"], f"{r['requests']:,}"]
            if wide:
                cells.append(F.tokens(r["input"]))
            cells += [F.tokens(r["read"]), F.tokens(r["write"]), F.tokens(r["output"]),
                      F.money(r["cost"])]
            t.add_row(*cells, bar(r["share"], bar_w) + Text(f" {r['share'] * 100:3.0f}%", style="dim"))
        return t

    bar_w = 10 if W >= 100 else 6

    # Header
    head = Table.grid(expand=True)
    head.add_column()
    head.add_column(justify="right")
    head.add_row(Text.assemble(("claude-info ", "bold cyan"), (d["build"], "dim")),
                 Text(f"pricing {d['pricing_date']}", style="dim"))
    console.print(head)

    # Session
    s = d["session"]
    if s:
        facts = Table.grid(padding=(0, 2))
        facts.add_column(style="dim")
        facts.add_column()
        if s["uncached"] > s["cost"] > 0:
            frac = 1 - s["cost"] / s["uncached"]
            facts.add_row("Cache", Text.assemble(
                ("saved ", ""), (F.money(s["uncached"] - s["cost"]), "bold green"), "  ",
                bar(frac, bar_w, "green"), (f" {frac * 100:.0f}%", "dim"),
                (f"  ({F.money(s['uncached'])} without caching)", "dim")))
        speed = Text()
        if s["main_tps"]:
            speed.append(f"{s['main_tps']:.0f}", style="bold")
            speed.append(" tok/s main", style="dim")
        agent_rates = [a["tps"] for a in s["agents"] if a["tps"]]
        if agent_rates:
            speed.append(f"   {sum(agent_rates) / len(agent_rates):.0f}", style="bold")
            speed.append(" tok/s avg per agent", style="dim")
        if speed.plain:
            facts.add_row("Speed", speed)
        split = Text.assemble(("main ", "dim"), (F.money(s["main_cost"]), "green"))
        if s["agents"]:
            split.append_text(Text.assemble(
                ("   subagents ", "dim"), (F.money(s["cost"] - s["main_cost"]), "green"),
                (f" across {len(s['agents'])}", "dim")))
        facts.add_row("Split", split)

        body = Group(models_table(s["models"], bar_w), Text(), facts)
        console.print(panel(body, f"Session {s['id'][:8]}", s["project"]))

        if s["agents"]:
            t = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True,
                      header_style="dim")
            t.add_column("Agent", ratio=1, no_wrap=True, overflow="ellipsis")
            t.add_column("Model")
            t.add_column("State")
            t.add_column("tok/s", justify="right")
            t.add_column("Cost", justify="right", style="green")
            for a in s["agents"]:
                t.add_row(a["desc"], a["model"],
                          Text("running", style="yellow") if a["running"] else Text("done", style="dim"),
                          f"{a['tps']:.0f}" if a["tps"] else "-", F.money(a["cost"]))
            running = sum(1 for a in s["agents"] if a["running"])
            console.print(panel(t, f"Subagents {len(s['agents'])}", f"{running} running" if running else ""))
    else:
        console.print(panel(Text(f"No session transcript found for {d['cwd']}", style="dim"), "Session"))

    # Spend over time and by model
    h = d["history"]
    spend = Table.grid(padding=(0, 2), expand=True)
    spend.add_column(style="dim")
    spend.add_column(justify="right", style="bold green")
    for label, key in (("today", "today"), ("7 days", "week"), ("30 days", "month"), ("all time", "all")):
        spend.add_row(label, F.money(h[key]))
    trend = Text.assemble(("\n", ""), (spark(h["trend"][:-1]), "cyan"), (spark(h["trend"])[-1:], "bold cyan"))
    trend.append(f"\n{TREND_DAYS} days, peak {F.money(max(h['trend']))}/day", style="dim")
    spend_panel = panel(Group(spend, trend), "Spend", f"{h['active_days']} active days")

    by_model = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True,
                     header_style="dim")
    by_model.add_column("Model", style="bold", no_wrap=True)
    by_model.add_column("Req", justify="right")
    by_model.add_column("Output", justify="right")
    by_model.add_column("Cost", justify="right", style="green")
    by_model.add_column("Share", no_wrap=True)
    for r in h["models"]:
        by_model.add_row(r["name"], f"{r['requests']:,}", F.tokens(r["output"]), F.money(r["cost"]),
                         bar(r["share"], bar_w) + Text(f" {r['share'] * 100:3.0f}%", style="dim"))
    model_panel = panel(by_model, "By model", "all projects")

    if W >= 96:
        row = Table.grid(expand=True, padding=(0, 1))
        row.add_column(ratio=1)
        row.add_column(ratio=2)
        row.add_row(spend_panel, model_panel)
        console.print(row)
    else:
        console.print(spend_panel)
        console.print(model_panel)

    # Projects
    if h["projects"]:
        t = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True,
                  header_style="dim")
        t.add_column("Project", ratio=1, no_wrap=True, overflow="ellipsis")
        t.add_column("Sessions", justify="right")
        t.add_column("Requests", justify="right")
        t.add_column("Cost", justify="right", style="green")
        t.add_column("Share", no_wrap=True)
        for p in h["projects"]:
            t.add_row(p["name"], str(p["sessions"]), f"{p['requests']:,}", F.money(p["cost"]),
                      bar(p["share"], bar_w) + Text(f" {p['share'] * 100:3.0f}%", style="dim"))
        console.print(panel(t, "Projects", f"top {len(h['projects'])}"))

    console.print(Text(NOTE, style="dim"))


# ── Plain fallback ───────────────────────────────────────────────────────────

def _table(rows: List[List[str]], align: str) -> List[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  " + "  ".join(c.ljust(w) if a == "l" else c.rjust(w)
                             for c, w, a in zip(r, widths, align)).rstrip() for r in rows]


def render_plain(d: dict) -> str:
    out = [f"claude-info {d['build']}  pricing {d['pricing_date']}",
           "(install the vendor/rich submodule for the full layout: git submodule update --init)", ""]
    s = d["session"]
    if s:
        out.append(f"Session {s['id'][:8]}  {s['project']}")
        out += _table([["model", "req", "read", "write", "output", "cost"]]
                      + [[r["name"], str(r["requests"]), F.tokens(r["read"]), F.tokens(r["write"]),
                          F.tokens(r["output"]), F.money(r["cost"])] for r in s["models"]], "lrrrrr")
        if s["uncached"] > s["cost"] > 0:
            out.append(f"  cache saved {F.money(s['uncached'] - s['cost'])}")
        if s["main_tps"]:
            out.append(f"  main {s['main_tps']:.0f} tok/s")
        for a in s["agents"]:
            state = "running" if a["running"] else "done"
            out.append(f"  agent {a['desc'][:40]}  {a['model']}  {state}  {F.money(a['cost'])}")
        out.append("")
    h = d["history"]
    out.append(f"Spend  today {F.money(h['today'])}  7d {F.money(h['week'])}  "
               f"30d {F.money(h['month'])}  all {F.money(h['all'])}  {spark(h['trend'])}")
    out += _table([["model", "req", "cost"]]
                  + [[r["name"], str(r["requests"]), F.money(r["cost"])] for r in h["models"]], "lrr")
    out += _table([["project", "req", "cost"]]
                  + [[p["name"], str(p["requests"]), F.money(p["cost"])] for p in h["projects"]], "lrr")
    out += ["", NOTE]
    return "\n".join(out)


def rich_available() -> bool:
    return (VENDOR_RICH / "rich" / "__init__.py").is_file()


def main(cwd: Optional[str] = None) -> int:
    d = collect(cwd or os.getcwd())
    if rich_available() and not os.environ.get("CLAUDE_INFO_PLAIN"):
        render_rich(d)
    else:
        print(render_plain(d))
    return 0
