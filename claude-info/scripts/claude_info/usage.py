"""Incremental transcript scanning.

Claude Code writes one JSONL line per content block, and every line of an
assistant response repeats the same `message.id` and `usage` object. Summing
lines naively double-counts most requests, so each request is counted once by
message id.

Each file remembers the byte offset it was read up to. On the next scan only
the appended bytes are parsed, which keeps the status line cheap even for
transcripts that are tens of megabytes. A file that shrank, or a pricing table
that changed, triggers a full rescan of that file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import pricing as pricing_mod

STATE_VERSION = 2

# Duplicate lines of one response are adjacent, so a short window of recent
# ids is enough to deduplicate without storing every id of every file.
RECENT_IDS = 64

# Bucket fields, in storage order.
FIELDS = ("input", "cw5m", "cw1h", "read", "output", "requests", "cost")


def pricing_signature(path: Path = pricing_mod.PRICING_FILE) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "none"


def local_day(ts: str) -> str:
    """ISO-8601 UTC timestamp -> local YYYY-MM-DD ("" when unparsable)."""
    if not ts:
        return ""
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return ""


def new_file_state() -> dict:
    return {"offset": 0, "size": 0, "mtime": 0, "recent": [], "buckets": {}, "last_model": "", "last_ts": ""}


def _add(bucket: List[float], vals: Iterable[float]) -> None:
    for i, v in enumerate(vals):
        bucket[i] += v


def parse_request(obj: dict) -> Optional[tuple]:
    """Return (message_id, model, Usage, timestamp) for an assistant line."""
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message") or {}
    u = msg.get("usage") or {}
    if not u:
        return None
    mid = msg.get("id") or obj.get("requestId") or obj.get("uuid") or ""
    model = msg.get("model") or "unknown"
    cc = int(u.get("cache_creation_input_tokens") or 0)
    split = u.get("cache_creation") or {}
    cw5 = int(split.get("ephemeral_5m_input_tokens") or 0)
    cw1 = int(split.get("ephemeral_1h_input_tokens") or 0)
    if cw5 + cw1 == 0 and cc > 0:
        cw5 = cc  # no split recorded: assume the default 5-minute TTL
    usage = pricing_mod.Usage(
        input=int(u.get("input_tokens") or 0),
        cw5m=cw5,
        cw1h=cw1,
        read=int(u.get("cache_read_input_tokens") or 0),
        output=int(u.get("output_tokens") or 0),
        fast=(u.get("speed") == "fast"),
        geo_us=(u.get("inference_geo") == "us"),
    )
    return mid, model, usage, obj.get("timestamp") or ""


def scan_file(path: Path, state: Optional[dict], prices: pricing_mod.Pricing) -> dict:
    """Bring one file's state up to date and return it."""
    try:
        st = path.stat()
    except OSError:
        return state or new_file_state()

    if state is None or st.st_size < state.get("offset", 0):
        state = new_file_state()
    if st.st_size == state["offset"]:
        state["size"], state["mtime"] = st.st_size, int(st.st_mtime)
        return state

    recent: List[str] = list(state.get("recent", []))
    recent_set = set(recent)
    buckets: Dict[str, List[float]] = state["buckets"]

    with path.open("rb") as f:
        f.seek(state["offset"])
        chunk = f.read()
    # Only consume complete lines; a partially written last line is picked
    # up on the next scan.
    end = chunk.rfind(b"\n")
    if end < 0:
        return state
    consumed = chunk[: end + 1]

    for raw in consumed.splitlines():
        if b'"usage"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        req = parse_request(obj)
        if req is None:
            continue
        mid, model, usage, ts = req
        if mid and mid in recent_set:
            continue
        if mid:
            recent.append(mid)
            recent_set.add(mid)
            if len(recent) > RECENT_IDS:
                recent_set.discard(recent.pop(0))
        key = f"{local_day(ts)}|{model}"
        b = buckets.setdefault(key, [0] * len(FIELDS))
        _add(b, (usage.input, usage.cw5m, usage.cw1h, usage.read, usage.output, 1,
                 prices.cost(model, usage)))
        if not model.startswith("<"):
            state["last_model"] = model
        state["last_ts"] = ts or state.get("last_ts", "")

    state["offset"] += len(consumed)
    state["size"], state["mtime"] = st.st_size, int(st.st_mtime)
    state["recent"] = recent
    return state


# ── Aggregation helpers ──────────────────────────────────────────────────────

def empty_totals() -> Dict[str, float]:
    return {k: 0 for k in FIELDS}


def merge(into: Dict[str, float], bucket: List[float]) -> None:
    for i, k in enumerate(FIELDS):
        into[k] += bucket[i]


def by_model(states: Iterable[dict]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for s in states:
        for key, b in s.get("buckets", {}).items():
            model = key.split("|", 1)[1]
            merge(out.setdefault(model, empty_totals()), b)
    return out


def by_day(states: Iterable[dict]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for s in states:
        for key, b in s.get("buckets", {}).items():
            day = key.split("|", 1)[0]
            merge(out.setdefault(day, empty_totals()), b)
    return out


def total(states: Iterable[dict]) -> Dict[str, float]:
    t = empty_totals()
    for s in states:
        for b in s.get("buckets", {}).values():
            merge(t, b)
    return t


# ── Persistence ──────────────────────────────────────────────────────────────

def load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_json(path: Path, data: dict) -> None:
    """Atomic write, so a concurrent reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_states(path: Path) -> Dict[str, dict]:
    """File states from a cache file, discarded when the version or the
    pricing table changed since they were written."""
    data = load_json(path) or {}
    if data.get("version") != STATE_VERSION or data.get("pricing") != pricing_signature():
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def save_states(path: Path, files: Dict[str, dict], extra: Optional[dict] = None) -> None:
    data = {"version": STATE_VERSION, "pricing": pricing_signature(), "files": files}
    if extra:
        data.update(extra)
    save_json(path, data)


# ── Session view (used by the status line on every refresh) ──────────────────

def session_files(transcript: Path) -> List[Path]:
    """Main transcript plus any subagent transcripts of the same session."""
    files = [transcript]
    sub = transcript.with_suffix("") / "subagents"
    if sub.is_dir():
        files.extend(sorted(sub.glob("*.jsonl")))
    return files


def scan_session(transcript: Path, cache_dir: Path, session_id: str,
                 prices: Optional[pricing_mod.Pricing] = None) -> dict:
    """Up-to-date per-file states for one session, persisted between calls."""
    prices = prices or pricing_mod.default()
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    cache = cache_dir / f"session-{safe_id}.json"
    states = load_states(cache)
    changed = False
    for p in session_files(transcript):
        key = str(p)
        before = states.get(key)
        before_off = before.get("offset") if before else None
        states[key] = scan_file(p, before, prices)
        changed |= states[key].get("offset") != before_off
    if changed:
        try:
            save_states(cache, states)
        except OSError:
            pass
    return {"main": states.get(str(transcript)) or new_file_state(),
            "all": list(states.values())}
