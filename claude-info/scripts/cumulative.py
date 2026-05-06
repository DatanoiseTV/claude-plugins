#!/usr/bin/env python3
"""
Walk every Claude Code transcript under ~/.claude/projects and produce
lifetime token + (estimated) cost totals, with a per-transcript cache
keyed on (mtime, size) so unchanged files aren't rescanned.

Cost is estimated from published Anthropic per-token rates (USD per 1M
tokens). The pricing snapshot is 2026-Q2 — edit RATES if rates change.
The harness's per-session `cost.total_cost_usd` may differ from this
sum (especially under subscription billing).

Usage:
    cumulative.py            -> TSV: cost in cc cr out msgs sessions
    cumulative.py --json     -> JSON aggregate
    cumulative.py --by-project   -> per-project rows (TSV)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable

CACHE_DIR = Path.home() / ".claude" / "cache"
CACHE_FILE = CACHE_DIR / "claude-info-totals.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Per-million-token USD rates: input, cache_5m, cache_1h, cache_read, output.
RATES = {
    "opus":    (15.00, 18.75, 30.00, 1.50, 75.00),
    "sonnet":  (3.00,  3.75,  6.00,  0.30, 15.00),
    "haiku45": (0.80,  1.00,  1.60,  0.08, 4.00),
    "haiku3":  (0.25,  0.30,  0.50,  0.03, 1.25),
}


def family(model: str) -> str:
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku-4" in m or "haiku-3-5" in m:
        return "haiku45"
    if "haiku" in m:
        return "haiku3"
    return "sonnet"


def cost_for(model: str, in_t: int, cw5: int, cw1: int, cr: int, out_t: int) -> float:
    r = RATES[family(model)]
    return (
        in_t  * r[0] / 1e6 +
        cw5   * r[1] / 1e6 +
        cw1   * r[2] / 1e6 +
        cr    * r[3] / 1e6 +
        out_t * r[4] / 1e6
    )


def scan_transcript(path: Path) -> dict:
    """Sum tokens + cost across every assistant message in one transcript."""
    cost = sum_in = sum_cc = sum_cr = sum_out = msgs = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                model = msg.get("model", "unknown")
                in_t = u.get("input_tokens", 0) or 0
                cc = u.get("cache_creation_input_tokens", 0) or 0
                cr = u.get("cache_read_input_tokens", 0) or 0
                out_t = u.get("output_tokens", 0) or 0
                # Cache 5m / 1h split if present.
                cc_obj = u.get("cache_creation") or {}
                cw5 = cc_obj.get("ephemeral_5m_input_tokens", 0) or 0
                cw1 = cc_obj.get("ephemeral_1h_input_tokens", 0) or 0
                if cw5 + cw1 == 0 and cc > 0:
                    cw5 = cc  # fall back to 5m rate when split missing
                cost += cost_for(model, in_t, cw5, cw1, cr, out_t)
                sum_in += in_t
                sum_cc += cc
                sum_cr += cr
                sum_out += out_t
                msgs += 1
    except FileNotFoundError:
        pass
    return {
        "cost": cost,
        "in": sum_in,
        "cc": sum_cc,
        "cr": sum_cr,
        "out": sum_out,
        "msgs": msgs,
    }


def load_cache() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text())
        if data.get("version") == 1 and isinstance(data.get("transcripts"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "transcripts": {}}


def save_cache(data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="claude-info-", dir=CACHE_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        finally:
            raise


def gather() -> tuple[dict, dict]:
    """Refresh cache for changed files, return (cache, aggregate)."""
    cache = load_cache()
    transcripts = cache.get("transcripts", {})
    new_transcripts: dict[str, dict] = {}

    for path in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        prev = transcripts.get(key)
        if prev and prev.get("mtime") == int(st.st_mtime) and prev.get("size") == st.st_size:
            new_transcripts[key] = prev
            continue
        sums = scan_transcript(path)
        new_transcripts[key] = {
            "mtime": int(st.st_mtime),
            "size": st.st_size,
            **sums,
        }

    agg = {
        "lifetime_cost_usd": sum(t["cost"] for t in new_transcripts.values()),
        "lifetime_input_tokens": sum(t["in"] for t in new_transcripts.values()),
        "lifetime_cache_creation": sum(t["cc"] for t in new_transcripts.values()),
        "lifetime_cache_read": sum(t["cr"] for t in new_transcripts.values()),
        "lifetime_output_tokens": sum(t["out"] for t in new_transcripts.values()),
        "message_count": sum(t["msgs"] for t in new_transcripts.values()),
        "session_count": len(new_transcripts),
    }

    # Per-project breakdown, keyed by the encoded project dir name as it
    # appears under ~/.claude/projects/ (e.g. "-Users-syso-dev-priv-foo").
    # The status line looks up the current project here in O(1).
    projects: dict[str, dict] = {}
    for path, e in new_transcripts.items():
        try:
            project = path.split("/projects/", 1)[1].split("/", 1)[0]
        except IndexError:
            continue
        p = projects.setdefault(project, {
            "cost": 0.0, "in": 0, "cc": 0, "cr": 0, "out": 0, "msgs": 0, "sessions": 0,
        })
        p["cost"] += e["cost"]
        p["in"] += e["in"]
        p["cc"] += e["cc"]
        p["cr"] += e["cr"]
        p["out"] += e["out"]
        p["msgs"] += e["msgs"]
        p["sessions"] += 1

    out = {
        "version": 1,
        "generated_at": int(__import__("time").time()),
        "transcripts": new_transcripts,
        "projects": projects,
        **agg,
    }
    save_cache(out)
    return out, agg


def by_project(cache: dict) -> Iterable[tuple]:
    by = {}
    for path, e in cache["transcripts"].items():
        try:
            project = path.split("/projects/", 1)[1].split("/", 1)[0]
        except IndexError:
            project = "(unknown)"
        b = by.setdefault(project, {"cost": 0.0, "in": 0, "cc": 0, "cr": 0, "out": 0, "msgs": 0, "sessions": 0})
        b["cost"] += e["cost"]
        b["in"] += e["in"]
        b["cc"] += e["cc"]
        b["cr"] += e["cr"]
        b["out"] += e["out"]
        b["msgs"] += e["msgs"]
        b["sessions"] += 1
    rows = sorted(
        ((p, v["cost"], v["in"], v["cc"], v["cr"], v["out"], v["msgs"], v["sessions"])
         for p, v in by.items()),
        key=lambda r: -r[1],
    )
    return rows


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "tsv"
    cache, agg = gather()
    if mode == "--json":
        print(json.dumps(agg, indent=2))
    elif mode == "--by-project":
        for p, cost, in_t, cc, cr, out_t, msgs, sessions in by_project(cache):
            print(f"{cost:.6f}\t{in_t}\t{cc}\t{cr}\t{out_t}\t{msgs}\t{sessions}\t{p}")
    else:
        print(f"{agg['lifetime_cost_usd']:.6f}\t{agg['lifetime_input_tokens']}\t"
              f"{agg['lifetime_cache_creation']}\t{agg['lifetime_cache_read']}\t"
              f"{agg['lifetime_output_tokens']}\t{agg['message_count']}\t"
              f"{agg['session_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
