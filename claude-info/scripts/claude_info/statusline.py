"""Two-line status line.

Line 1 answers "what is running where": model and its speed, other models
used in this session, location, git, PR, running subagents.
Line 2 answers "what is it costing me": context fill, cost and burn rate,
prompt-cache state, rate-limit pacing, lifetime totals.

Segments that only matter when something is off (a cold cache, a limit
that will run out, a rebase in progress) stay hidden otherwise. Each
segment has a priority; when the terminal is too narrow the lowest
priorities are dropped first.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import box, config, gitinfo, pricing, totals, usage
from . import fmt as F
from .limits import pace


@dataclass
class Seg:
    text: str
    prio: int


@dataclass
class Ctx:
    """Everything a segment might need, gathered once per refresh."""

    j: dict
    cfg: dict
    now: float
    prices: pricing.Pricing
    session: Optional[dict] = None   # usage.scan_session result
    summary: Optional[dict] = None   # totals summary
    cols: Optional[int] = None
    graphs: bool = False             # draw sparklines, gauges and spinners

    def get(self, *path, default=None):
        cur = self.j
        for p in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(p)
        return default if cur is None else cur

    def show(self, key: str) -> bool:
        return bool(self.cfg.get("show", {}).get(key, True))


def short_model_name(ctx: Ctx, model_id: str) -> str:
    info = ctx.prices.info(model_id)
    if info and info.get("name"):
        return info["name"]
    return model_id.replace("claude-", "")


# ── Line 1 ───────────────────────────────────────────────────────────────────

def seg_model(ctx: Ctx) -> Optional[Seg]:
    name = (ctx.get("model", "display_name")
            or short_model_name(ctx, ctx.get("model", "id", default="")) or "Claude")
    name = name.replace("Claude ", "")
    out = F.paint(name, F.BOLD, F.CYAN)
    if ctx.get("fast_mode"):
        out += " " + F.paint("fast", F.YELLOW)
    effort = ctx.get("effort", "level")
    if effort:
        out += F.paint(f" {effort}", F.DIM)
    agent = ctx.get("agent", "name")
    if agent:
        out += F.paint(f" @{agent}", F.DIM)
    return Seg(out, 100)


def seg_speed(ctx: Ctx) -> Optional[Seg]:
    if not ctx.session:
        return None
    tps = usage.tokens_per_second(ctx.session["main"])
    if tps is None:
        return None
    out = F.paint(f"{tps:.0f} t/s", F.DIM)
    if ctx.graphs and ctx.cfg["rate_spark_len"] > 0:
        hist = usage.rate_history(ctx.session["main"])[-ctx.cfg["rate_spark_len"]:]
        if len(hist) > 1:
            out += " " + F.paint(F.spark(hist), F.CYAN)
    return Seg(out, 45)


_PHASE_STYLE = {"thinking": F.MAGENTA, "writing": F.CYAN, "tools": F.YELLOW}


def seg_phase(ctx: Ctx) -> Optional[Seg]:
    """What the main thread is doing right now, with a spinner and how long
    it has been at it. Absent when the turn is over."""
    if not ctx.session:
        return None
    ph = usage.phase(ctx.session["main"], ctx.now, ctx.cfg["phase_stale_s"])
    if ph is None:
        return None
    name, age = ph
    color = _PHASE_STYLE.get(name, F.CYAN)
    spin = F.spinner(ctx.now, ctx.cfg["spinner_fps"])
    label = name
    act = usage.activity(ctx.session["main"])
    if name == "tools" and act:
        # Say which tool and on what, rather than just "tools".
        label = act[: ctx.cfg["activity_title_max"]]
        if len(act) > len(label):
            label = label[:-1] + "…"
    return Seg(F.paint(f"{spin} {label}", color) + F.paint(f" {F.duration(age)}", F.DIM), 92)


def seg_context_trend(ctx: Ctx) -> Optional[Seg]:
    """Context size over the last requests and its average growth, which
    tells how fast the window is filling (and shows compactions as drops)."""
    if not ctx.session or not ctx.graphs:
        return None
    hist = (ctx.session["main"].get("ctx_hist") or [])[-ctx.cfg["context_spark_len"]:]
    if len(hist) < 3:
        return None
    out = F.paint(F.spark(hist, floor_zero=False), F.BLUE)
    growth = (hist[-1] - hist[0]) / (len(hist) - 1)
    if growth >= 0:
        out += F.paint(f" +{F.tokens(growth)}/req", F.DIM)
    else:
        out += F.paint(" compacted", F.DIM)
    return Seg(out, 60)


def seg_model_mix(ctx: Ctx) -> Optional[Seg]:
    """Other models this session has spent on, by share of session cost.
    Covers /model switches as well as subagents on a different model."""
    if not ctx.session or not ctx.show("model_mix"):
        return None
    models = usage.by_model(ctx.session["all"])
    current = ctx.prices.resolve(ctx.get("model", "id", default=""))
    # Fold dated / suffixed ids into their pricing key.
    shares: dict = {}
    for mid, t in models.items():
        key = ctx.prices.resolve(mid)
        if key is None:
            continue
        shares[key] = shares.get(key, 0.0) + t["cost"]
    total_cost = sum(shares.values())
    if total_cost <= 0 or len(shares) < 2:
        return None
    others = sorted(((c, k) for k, c in shares.items() if k != current), reverse=True)
    parts = []
    for cost, key in others[: ctx.cfg["model_mix_max"]]:
        share = cost / total_cost
        if share >= ctx.cfg["model_mix_min_share"]:
            parts.append(f"{short_model_name(ctx, key)} {share * 100:.0f}%")
    if not parts:
        return None
    return Seg(F.paint("+" + " +".join(parts), F.DIM), 40)


def seg_location(ctx: Ctx) -> Optional[Seg]:
    cwd = ctx.get("workspace", "current_dir") or ctx.get("cwd", default="")
    proj = ctx.get("workspace", "project_dir", default="")
    if proj and cwd == proj:
        text = Path(proj).name
    elif proj and cwd.startswith(proj.rstrip("/") + "/"):
        text = f"{Path(proj).name} ▸ {cwd[len(proj.rstrip('/')) + 1:]}"
    else:
        home = str(Path.home())
        text = "~" + cwd[len(home):] if cwd == home or cwd.startswith(home + "/") else cwd
        parts = text.split("/")
        if len(parts) > 3:
            text = "…/" + "/".join(parts[-2:])
    out = F.paint(text, F.BLUE)
    added = ctx.get("workspace", "added_dirs", default=[]) or []
    if added:
        out += F.paint(f" +{len(added)}", F.DIM)
    return Seg(out, 90)


def seg_git(ctx: Ctx) -> Optional[Seg]:
    if not ctx.show("git"):
        return None
    cwd = ctx.get("workspace", "current_dir") or ctx.get("cwd", default="")
    g = gitinfo.read(cwd)
    if g is None:
        return None
    out = F.paint(g.branch, F.MAGENTA)
    if g.operation:
        out += " " + F.paint(g.operation, F.BOLD, F.RED)
    if g.ahead:
        out += F.paint(f" ↑{g.ahead}", F.CYAN)
    if g.behind:
        out += F.paint(f" ↓{g.behind}", F.YELLOW)
    if g.conflicts:
        out += F.paint(f" !{g.conflicts}", F.RED)
    if g.staged:
        out += F.paint(f" +{g.staged}", F.GREEN)
    if g.modified:
        out += F.paint(f" ~{g.modified}", F.YELLOW)
    if g.untracked:
        out += F.paint(f" ?{g.untracked}", F.DIM)
    if ctx.get("workspace", "git_worktree"):
        out += F.paint(" wt", F.DIM)
    return Seg(out, 70)


_PR_STATE = {
    "approved": (F.GREEN, "approved"),
    "changes_requested": (F.RED, "changes"),
    "pending": (F.YELLOW, "review"),
    "draft": (F.DIM, "draft"),
}


def seg_pr(ctx: Ctx) -> Optional[Seg]:
    num = ctx.get("pr", "number")
    if not num or not ctx.show("pr"):
        return None
    prefix = "!" if ctx.get("pr", "kind") == "mr" else "#"
    color, label = _PR_STATE.get(ctx.get("pr", "review_state", default=""), (F.CYAN, ""))
    text = F.link(f"{prefix}{num}", ctx.get("pr", "url", default=""))
    out = F.paint(text, color)
    if label:
        out += F.paint(f" {label}", color)
    return Seg(out, 50)


def agent_states(ctx: Ctx) -> List[dict]:
    """Subagents of this session with what each is doing.

    status is one of:
      working  wrote to its transcript recently (phase says what it does)
      tools    inside a tool call for longer than the stall threshold;
               normal for builds and tests, so it is not called stalled
      stalled  no output for longer than the stall threshold otherwise
      waiting  turn ended but the agent has not reported back yet
      done     finished; kept on screen briefly, then dropped
    Agents silent for longer than agent_stale_s are left out entirely.
    """
    if not ctx.session:
        return []
    cfg = ctx.cfg
    out = []
    for path in sorted(ctx.session.get("files", {})):  # stable row order
        st = ctx.session["files"][path]
        if st is ctx.session["main"] or not path.endswith(".jsonl"):
            continue
        meta = usage.load_json(Path(path[: -len(".jsonl")] + ".meta.json")) or {}
        quiet = max(0.0, ctx.now - st.get("mtime", 0))
        ph = usage.phase(st, ctx.now, float("inf"))
        if meta.get("finished") or usage.handed_back(st):
            if quiet > cfg["agent_done_show_s"]:
                continue
            status, label = "done", "done"
        elif quiet > cfg["agent_stale_s"]:
            continue
        elif ph is None:
            status, label = "waiting", f"waiting {F.duration(quiet)}"
        elif quiet > cfg["agent_stall_s"]:
            # Long tool calls are normal (builds, tests), up to a point.
            if ph[0] == "tools" and quiet <= cfg["agent_tool_stall_s"]:
                status, label = "tools", f"tools {F.duration(ph[1])}"
            else:
                status, label = "stalled", f"stalled {F.duration(quiet)}"
        else:
            status, label = "working", f"{ph[0]} {F.duration(ph[1])}"
        out.append({
            "title": meta.get("description") or Path(path).stem,
            # What it is doing now; the title only says what it was asked.
            "desc": usage.activity(st) or meta.get("description") or Path(path).stem,
            "files": [Path(f).name for f in usage.edited_files(st)],
            "model": short_model_name(ctx, st.get("last_model", "")) if st.get("last_model") else "",
            "status": status,
            "label": label,
            "phase": ph[0] if ph else "",
            "tps": usage.tokens_per_second(st),
            "cost": usage.total([st])["cost"],
        })
    return out


def running_agents(ctx: Ctx) -> List[dict]:
    return [a for a in agent_states(ctx) if a["status"] != "done"]


_AGENT_STYLE = {"working": F.CYAN, "tools": F.YELLOW, "stalled": F.RED,
                "waiting": F.DIM, "done": F.DIM}


def seg_agents(ctx: Ctx) -> Optional[Seg]:
    """One-cell summary: count, stalled count and output rates."""
    agents = running_agents(ctx)
    if not agents:
        return None
    label = f"{len(agents)} agent" + ("s" if len(agents) != 1 else "")
    spin = F.spinner(ctx.now + 0.5, ctx.cfg["spinner_fps"]) + " " if ctx.graphs else ""
    out = F.paint(spin + label, F.CYAN)
    stalled = sum(1 for a in agents if a["status"] == "stalled")
    if stalled:
        out += F.paint(f" {stalled} stalled", F.RED)
    known = [a["tps"] for a in agents if a["tps"]]
    if not known:
        return Seg(out, 60)
    if len(agents) <= ctx.cfg["agents_max_rates"]:
        rates = "·".join(f"{a['tps']:.0f}" if a["tps"] else "–" for a in agents)
        out += F.paint(f" {rates} t/s", F.DIM)
    else:
        # Many agents: their combined output rate says more than a list.
        out += F.paint(f" Σ{sum(known):.0f} t/s", F.DIM)
    return Seg(out, 60)


def agent_rows(ctx: Ctx, inner: int) -> List[str]:
    """Per-agent table for the box: marker, description, model, state, rate
    and cost, aligned in columns and fitted to the inner width."""
    # Problems first, then active, then finished; spawn order within each,
    # so rows only move when an agent changes state.
    rank = {"stalled": 0, "working": 1, "tools": 1, "waiting": 2, "done": 3}
    agents = sorted(agent_states(ctx), key=lambda a: rank[a["status"]])
    if not agents:
        return []
    cap = ctx.cfg["agents_max_rows"]
    shown = agents if len(agents) <= cap else agents[: cap - 1]

    def marker(a: dict) -> str:
        if a["status"] in ("working", "tools"):
            return F.paint(F.spinner(ctx.now + hash(a["desc"]) % 7, ctx.cfg["spinner_fps"]),
                           _AGENT_STYLE[a["status"]])
        if a["status"] == "stalled":
            return F.paint("!", F.BOLD, F.RED)
        return F.paint("·", F.DIM)

    nfiles = ctx.cfg["agent_files_shown"]
    cols = {
        "files": [", ".join(a["files"][:nfiles]) for a in shown],
        "model": [a["model"] for a in shown],
        "state": [a["label"] for a in shown],
        "rate": [f"{a['tps']:.0f} t/s" if a["tps"] else "" for a in shown],
        "cost": [F.money(a["cost"]) for a in shown],
    }
    widths = {k: max((len(v) for v in vs), default=0) for k, vs in cols.items()}
    widths["files"] = min(widths["files"], ctx.cfg["agent_files_width"])
    if not widths["files"]:
        widths.pop("files")
    gap = 2
    min_desc = 10
    # Drop the least useful columns until the description gets some room.
    order = ["model", "cost", "files", "rate"]
    while True:
        fixed = 2 + sum(w + gap for w in widths.values())
        if inner - fixed >= min_desc or not order:
            break
        widths.pop(order.pop(0))
    # The description only takes what it needs; spare width stays at the end
    # of the row instead of pushing the numbers to the far edge.
    longest = max(len(a["desc"]) for a in shown)
    desc_w = max(1, min(longest, inner - 2 - sum(w + gap for w in widths.values())))

    rows = []
    for i, a in enumerate(shown):
        desc = a["desc"] if len(a["desc"]) <= desc_w else a["desc"][: desc_w - 1] + "…"
        style = _AGENT_STYLE[a["status"]]
        line = marker(a) + " " + F.paint(desc.ljust(desc_w), F.DIM if a["status"] == "done" else "")
        if "files" in widths:
            f = cols["files"][i]
            f = f if len(f) <= widths["files"] else f[: widths["files"] - 1] + "…"
            line += " " * gap + F.paint(f.ljust(widths["files"]), F.BLUE)
        if "model" in widths:
            line += " " * gap + F.paint(cols["model"][i].ljust(widths["model"]), F.DIM)
        if "state" in widths:
            line += " " * gap + F.paint(cols["state"][i].ljust(widths["state"]), style)
        if "rate" in widths:
            line += " " * gap + F.paint(cols["rate"][i].rjust(widths["rate"]), F.DIM)
        if "cost" in widths:
            line += " " * gap + F.paint(cols["cost"][i].rjust(widths["cost"]), F.GREEN)
        rows.append(line)
    if len(agents) > len(shown):
        rest = agents[len(shown):]
        stalled = sum(1 for a in rest if a["status"] == "stalled")
        more = f"  +{len(rest)} more" + (f", {stalled} stalled" if stalled else "")
        rows.append(F.paint(more, F.RED if stalled else F.DIM))
    return rows


def seg_edited(ctx: Ctx) -> Optional[Seg]:
    """Files the main thread edited most recently."""
    if not ctx.session or not ctx.show("edited_files"):
        return None
    files = [Path(f).name for f in usage.edited_files(ctx.session["main"])]
    if not files:
        return None
    shown = files[: ctx.cfg["main_files_shown"]]
    more = f" +{len(files) - len(shown)}" if len(files) > len(shown) else ""
    return Seg(F.paint("✎ " + ", ".join(shown), F.BLUE) + F.paint(more, F.DIM), 30)


def seg_session_name(ctx: Ctx) -> Optional[Seg]:
    name = ctx.get("session_name")
    if not name or not ctx.show("session_name"):
        return None
    limit = ctx.cfg["session_name_max"]
    if len(name) > limit:
        name = name[: limit - 1] + "…"
    return Seg(F.paint(name, F.DIM), 10)


# ── Line 2 ───────────────────────────────────────────────────────────────────

def context_tokens(ctx: Ctx) -> int:
    total = ctx.get("context_window", "total_input_tokens")
    if total:
        return int(total)
    cu = ctx.get("context_window", "current_usage", default={}) or {}
    return int((cu.get("input_tokens") or 0) + (cu.get("cache_creation_input_tokens") or 0)
               + (cu.get("cache_read_input_tokens") or 0))


def seg_context(ctx: Ctx) -> Optional[Seg]:
    size = int(ctx.get("context_window", "context_window_size", default=200000))
    used = context_tokens(ctx)
    pct = ctx.get("context_window", "used_percentage")
    pct = float(pct) if pct is not None else (100.0 * used / size if size else 0.0)
    color = F.tier(pct, ctx.cfg["context_warn_pct"], ctx.cfg["context_crit_pct"])
    narrow = ctx.cols is not None and ctx.cols < ctx.cfg["narrow_columns"]
    width = ctx.cfg["bar_width_narrow"] if narrow else ctx.cfg["bar_width"]
    out = (F.paint(F.bar(pct, width), color) + " " + F.paint(f"{pct:.0f}%", F.BOLD, color)
           + F.paint(f" {F.tokens(used)}/{F.tokens(size)}", F.DIM))
    return Seg(out, 100)


def seg_cost(ctx: Ctx) -> Optional[Seg]:
    cost = float(ctx.get("cost", "total_cost_usd", default=0))
    return Seg(F.paint(F.money(cost), F.GREEN), 95)


def seg_burn(ctx: Ctx) -> Optional[Seg]:
    if not ctx.show("burn_rate"):
        return None
    cost = float(ctx.get("cost", "total_cost_usd", default=0))
    hours = float(ctx.get("cost", "total_duration_ms", default=0)) / 3.6e6
    if hours * 60 < ctx.cfg["burn_min_minutes"] or cost <= 0:
        return None
    return Seg(F.paint(f"{F.money(cost / hours)}/h", F.DIM), 35)


_MISS_CAUSES = {
    "tools_changed": "tools changed",
    "system_prompt_changed": "system prompt changed",
    "ttl_expired_5m": "5m ttl expired",
    "ttl_expired_1h": "1h ttl expired",
    "likely_server_side": "server side",
}


def recache_cost(ctx: Ctx, tokens: int) -> float:
    """What writing `tokens` to the cache costs on the current model."""
    model = ctx.get("model", "id", default="")
    # The usage passed in only selects the price tier (long prompt, fast).
    rates = ctx.prices.rates(model, pricing.Usage(cw5m=tokens, fast=bool(ctx.get("fast_mode"))))
    if rates is None:
        return 0.0
    per_m = rates.cw1h if ctx.get("prompt_cache", "ttl") == "1h" else rates.cw5m
    return tokens * per_m / 1e6


def seg_cache(ctx: Ctx) -> Optional[Seg]:
    pc = ctx.get("prompt_cache", default=None)
    if not pc or not ctx.show("cache") or not pc.get("caching_observed"):
        return None
    cfg = ctx.cfg
    parts: List[str] = []

    # Caches are per model: after a /model switch the next request writes the
    # whole context again, even though the old model's cache is still warm.
    last_model = (ctx.session or {}).get("main", {}).get("last_model", "")
    current = ctx.get("model", "id", default="")
    switched = bool(last_model and current
                    and ctx.prices.resolve(last_model) != ctx.prices.resolve(current))

    if switched:
        tokens = context_tokens(ctx)
        parts.append(F.paint(f"new model: next turn re-caches {F.tokens(tokens)} "
                             f"≈{F.money(recache_cost(ctx, tokens))}", F.YELLOW))
    elif not pc.get("warm"):
        tokens = int(pc.get("recache_tokens_if_cold") or context_tokens(ctx))
        parts.append(F.paint(f"cache cold: next turn ≈{F.money(recache_cost(ctx, tokens))}", F.YELLOW))
    else:
        exp = pc.get("expires_at")
        if exp:
            left = float(exp) - ctx.now
            color = F.YELLOW if left <= cfg["cache_expiry_warn_s"] else F.DIM
            if ctx.graphs:
                ttl = 3600.0 if pc.get("ttl") == "1h" else 300.0
                parts.append(F.paint("cache ", color)
                             + F.drain(left / ttl, cfg["cache_drain_width"],
                                       F.YELLOW if left <= cfg["cache_expiry_warn_s"] else F.GREEN)
                             + F.paint(f" {F.duration(left)}", color))
            elif left <= cfg["cache_countdown_s"]:
                parts.append(F.paint(f"cache {F.duration(left)}", color))

    last_miss = pc.get("last_miss_at")
    if last_miss and ctx.now - float(last_miss) <= cfg["cache_miss_recent_s"] and not switched:
        causes = (pc.get("last_miss_cause") or {}).get("causes") or []
        why = ", ".join(_MISS_CAUSES.get(c, c.replace("_", " ")) for c in causes) or "miss"
        parts.append(F.paint(f"miss: {why}", F.YELLOW))

    hit = pc.get("hit_ratio")
    if (hit is not None and int(pc.get("requests") or 0) >= cfg["cache_hit_min_requests"]
            and float(hit) < cfg["cache_hit_warn"]):
        parts.append(F.paint(f"hit {float(hit) * 100:.0f}%", F.DIM))

    if not parts:
        return None
    return Seg(" ".join(parts), 75)


def _limit_text(ctx: Ctx, label: str, used: float, resets_at, window_s: float) -> str:
    cfg = ctx.cfg
    p = pace(used, resets_at, window_s, ctx.now, cfg["pace_min_elapsed"])
    heading = p.projected if p.projected is not None else used
    color = F.tier(heading, cfg["limit_warn_pct"], cfg["limit_crit_pct"])
    out = F.paint(label, color) + " "
    if ctx.graphs:
        elapsed = None
        if p.reset_in is not None and window_s > 0:
            elapsed = 100.0 * (1 - p.reset_in / window_s)
        out += F.gauge(used, elapsed, cfg["limit_gauge_width"], color) + " "
    out += F.paint(f"{used:.0f}%", color)
    if p.will_hit:
        out += F.paint(f" full in {F.duration(p.full_in)}", F.BOLD, F.RED)
        out += F.paint(f" ↻{F.duration(p.reset_in)}", F.DIM)
    elif p.projected is not None and p.projected - used >= 5:
        out += F.paint(f"→{min(p.projected, 999):.0f}%", color)
    if not p.will_hit and p.reset_in is not None and used >= cfg["limit_warn_pct"]:
        out += F.paint(f" ↻{F.duration(p.reset_in)}", F.DIM)
    return out


def seg_windows(ctx: Ctx) -> List[Seg]:
    """5-hour and 7-day subscription windows."""
    if not ctx.show("limits"):
        return []
    rl = ctx.get("rate_limits", default={}) or {}
    windows = ctx.cfg["windows_s"]
    segs = []
    for key, label, prio in (("five_hour", "5h", 85), ("seven_day", "7d", 80)):
        w = rl.get(key)
        if w and w.get("used_percentage") is not None:
            segs.append(Seg(_limit_text(ctx, label, float(w["used_percentage"]),
                                        w.get("resets_at"), windows[key]), prio))
    return segs


def seg_spend(ctx: Ctx) -> Optional[Seg]:
    """Gateway spend limit, when one applies."""
    spend = (ctx.get("rate_limits", default={}) or {}).get("spend_limit")
    if not spend or spend.get("used_percentage") is None or not ctx.show("limits"):
        return None
    days = ctx.cfg["spend_period_days"].get(spend.get("period", ""), 0)
    text = _limit_text(ctx, "spend", float(spend["used_percentage"]),
                       spend.get("resets_at"), days * 86400)
    if spend.get("used_usd") is not None and spend.get("limit_usd"):
        text += F.paint(f" {F.money(spend['used_usd'])}/{F.money(spend['limit_usd'])}", F.DIM)
    return Seg(text, 85)


def seg_limits(ctx: Ctx) -> List[Seg]:
    return _flat(seg_windows(ctx), seg_spend(ctx))


def seg_lines(ctx: Ctx) -> Optional[Seg]:
    add = int(ctx.get("cost", "total_lines_added", default=0))
    rm = int(ctx.get("cost", "total_lines_removed", default=0))
    if not (add or rm) or not ctx.show("lines"):
        return None
    return Seg(F.paint(f"+{add}", F.GREEN) + F.paint(f" −{rm}", F.RED), 20)


def seg_lifetime(ctx: Ctx) -> List[Seg]:
    if not ctx.summary or not ctx.show("lifetime"):
        return []
    segs = []
    today = time.strftime("%Y-%m-%d", time.localtime(ctx.now))
    days = ctx.summary.get("days") or {}
    day = days.get(today)
    if day and day.get("cost"):
        text = F.paint(f"today {F.money(day['cost'])}", F.DIM)
        if ctx.graphs:
            n = ctx.cfg["spend_spark_days"]
            series = [days.get(time.strftime("%Y-%m-%d", time.localtime(ctx.now - i * 86400)), {})
                      .get("cost", 0.0) for i in range(n - 1, -1, -1)]
            text += " " + F.paint(F.spark(series), F.GREEN)
        segs.append(Seg(text, 25))
    tp = ctx.get("transcript_path", default="")
    if tp:
        proj = (ctx.summary.get("projects") or {}).get(Path(tp).parent.name)
        if proj and proj.get("cost"):
            segs.append(Seg(F.paint(f"Σ{F.money(proj['cost'])}", F.DIM), 15))
    return segs


def seg_duration(ctx: Ctx) -> Optional[Seg]:
    ms = float(ctx.get("cost", "total_duration_ms", default=0))
    if ms <= 0 or not ctx.show("duration"):
        return None
    return Seg(F.paint(F.duration(ms / 1000), F.DIM), 5)


# ── Layout ───────────────────────────────────────────────────────────────────

SEP = "  "


def fit(segs: List[Seg], width: Optional[int]) -> str:
    """Join segments, dropping the lowest priorities until the line fits."""
    segs = [s for s in segs if s and s.text]
    if width is None:
        return SEP.join(s.text for s in segs)
    while len(segs) > 1 and F.visible_len(SEP.join(s.text for s in segs)) > width:
        lowest = min(range(len(segs)), key=lambda i: segs[i].prio)
        segs.pop(lowest)
    return SEP.join(s.text for s in segs)


def _flat(*items) -> List[Seg]:
    out: List[Seg] = []
    for it in items:
        if isinstance(it, list):
            out.extend(it)
        elif it is not None:
            out.append(it)
    return out


def render_lines(ctx: Ctx, width: Optional[int]) -> str:
    """Compact two-line layout without a frame."""
    line1 = _flat(seg_model(ctx), seg_speed(ctx), seg_model_mix(ctx), seg_location(ctx),
                  seg_git(ctx), seg_pr(ctx), seg_agents(ctx), seg_session_name(ctx))
    line2 = _flat(seg_context(ctx), seg_cost(ctx), seg_burn(ctx), seg_cache(ctx),
                  seg_limits(ctx), seg_lines(ctx), seg_lifetime(ctx), seg_duration(ctx))
    return fit(line1, width) + "\n" + fit(line2, width)


def _cell(*segs: Optional[Seg]) -> str:
    return SEP.join(s.text for s in segs if s and s.text)


def _parts(*segs) -> List[box.Part]:
    return [box.Part(s.text, s.prio) for s in _flat(*segs)]


def render_box(ctx: Ctx, width: Optional[int]) -> str:
    """Framed grid: identity in the top border, a two-row grid of context,
    cost, limits and activity, session facts in the bottom border."""
    windows = seg_windows(ctx)
    lifetime = seg_lifetime(ctx)

    columns = [
        box.Column([_cell(seg_context(ctx)), _cell(seg_context_trend(ctx))], prio=100),
        box.Column([_cell(seg_cost(ctx), seg_burn(ctx)), _cell(*lifetime)], prio=95),
        box.Column([_cell(windows[0]) if windows else "", _cell(windows[1]) if len(windows) > 1 else ""],
                   prio=85),
        box.Column([_cell(seg_spend(ctx)), ""], prio=80),
        # Agents get their own section below the grid; the cell would repeat it.
        box.Column([_cell(seg_cache(ctx)), ""], prio=75),
    ]
    left = _parts(seg_model(ctx), seg_phase(ctx), seg_speed(ctx))
    right = _parts(seg_location(ctx), seg_git(ctx), seg_pr(ctx))
    footer = _parts(seg_model_mix(ctx), seg_lines(ctx), seg_edited(ctx), seg_duration(ctx),
                    seg_session_name(ctx))
    detail = (lambda inner: agent_rows(ctx, inner)) if ctx.show("agents") else None
    return box.render(left, right, columns, footer, width, detail)


def render(ctx: Ctx) -> str:
    width = ctx.cols - ctx.cfg["right_margin"] if ctx.cols else None
    if ctx.cfg.get("layout") == "lines":
        return render_lines(ctx, width)
    return render_box(ctx, width)


def build_context(j: dict, now: Optional[float] = None) -> Ctx:
    cfg = config.load()
    ctx = Ctx(j=j, cfg=cfg, now=now or time.time(), prices=pricing.default())
    ctx.graphs = cfg.get("layout") != "lines"
    try:
        ctx.cols = int(os.environ.get("COLUMNS", ""))
    except ValueError:
        ctx.cols = None

    tp = j.get("transcript_path")
    if tp and Path(tp).is_file():
        try:
            ctx.session = usage.scan_session(Path(tp), config.cache_dir(),
                                             j.get("session_id") or Path(tp).stem, ctx.prices)
        except (OSError, ValueError):
            ctx.session = None

    if ctx.show("lifetime"):
        ctx.summary = totals.load_summary()
        totals.refresh_in_background_if_stale(cfg)
    return ctx


def main() -> int:
    raw = sys.stdin.read()
    try:
        j = json.loads(raw) if raw.strip() else {}
    except ValueError:
        j = {}
    try:
        print(render(build_context(j)))
    except Exception as e:  # never leave the status line blank
        if os.environ.get("CLAUDE_INFO_DEBUG"):
            raise
        model = ((j.get("model") or {}).get("display_name")) or "claude"
        print(f"{model}  claude-info: {type(e).__name__}: {e}")
    return 0
