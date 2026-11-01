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
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import pricing as pricing_mod

STATE_VERSION = 4

# Duplicate lines of one response are adjacent, so a short window of recent
# ids is enough to deduplicate without storing every id of every file.
RECENT_IDS = 64

# Bucket fields, in storage order.
FIELDS = ("input", "cw5m", "cw1h", "read", "output", "requests", "cost")

# Throughput samples kept per file; the displayed rate is their weighted mean
# and the sparkline shows each of them.
SPEED_SAMPLES = 12
# Context size of the most recent requests, for the growth sparkline.
CONTEXT_HISTORY = 24
# Requests shorter than this, or with fewer output tokens, say more about
# latency than throughput and are left out of the rate.
SPEED_MIN_SECONDS = 1.0
SPEED_MIN_TOKENS = 32

# A request starts at its `api-request` line (or, in older transcripts, the
# user/tool-result line that triggered it). Only the timestamp is needed, so
# it is pulled with a regex instead of parsing those often large lines.
_TS_RE = re.compile(rb'"timestamp":\s*"([^"]+)"')
_START_RE = re.compile(rb'"type":\s*"(?:api-request|user)"')
_API_REQUEST_RE = re.compile(rb'"type":\s*"api-request"')

# What the main thread is doing, from the last transcript line:
#   thinking  request sent, nothing written yet (or a thinking block)
#   writing   text blocks arriving
#   tools     the response asked for tools, which are running now
#   idle      the turn ended
_BLOCK_PHASE = {"thinking": "thinking", "redacted_thinking": "thinking",
                "text": "writing", "tool_use": "tools", "server_tool_use": "tools"}


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


def epoch(ts: str) -> Optional[float]:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def new_file_state() -> dict:
    return {"offset": 0, "size": 0, "mtime": 0, "recent": [], "buckets": {},
            "last_model": "", "last_ts": "",
            # Throughput tracking: start of the pending request, the response
            # currently being written [id, start, last_line, output_tokens],
            # and finished [output_tokens, seconds] samples.
            "req_start": None, "cur": None, "samples": [],
            # [phase, since_epoch] and recent context sizes per request.
            "phase": ["idle", 0], "ctx_hist": []}


ACTIVITY_MAX = 120
# Recently edited files kept per transcript, most recent first.
EDITED_FILES = 5
_EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
# Tool calls that end a subagent's work rather than describe it.
_HANDBACK_TOOLS = ("SubagentHandback",)


def _clip(text: str, n: int = ACTIVITY_MAX) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _main_command(command: str) -> str:
    """The part of a shell command that says what it does: first line,
    without leading `cd dir &&` / `cd dir;` steps."""
    line = command.strip().splitlines()[0] if command.strip() else ""
    parts = [p.strip() for p in re.split(r"&&|;", line) if p.strip()]
    while len(parts) > 1 and parts[0].startswith("cd "):
        parts.pop(0)
    return " && ".join(parts)


def summarize_tool(name: str, inp: dict) -> str:
    """Short description of a tool call: what it acts on, not how."""
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Bash", "Monitor"):
        cmd = inp.get("description") or _main_command(inp.get("command") or "")
        return _clip(f"{name}: {cmd}") if cmd else name
    for key in ("file_path", "notebook_path", "path"):
        if inp.get(key):
            return _clip(f"{name} {Path(str(inp[key])).name}")
    for key in ("pattern", "query", "url", "description", "skill", "prompt"):
        if inp.get(key):
            return _clip(f'{name} "{inp[key]}"' if key in ("pattern", "query") else f"{name} {inp[key]}")
    return name


def edited_files(state: dict) -> List[str]:
    """Paths of recently edited files, most recent first."""
    return list(state.get("edited") or [])


def handed_back(state: dict) -> bool:
    return bool(state.get("handed_back"))


def activity(state: dict) -> str:
    """What the transcript's thread did last, e.g. 'Edit sdma.c'."""
    a = state.get("activity")
    return a if isinstance(a, str) else ""


def _set_phase(state: dict, name: str, t: Optional[float]) -> None:
    """Record the current phase; the start time only moves on a change."""
    if t is None:
        return
    cur = state.get("phase") or ["idle", 0]
    if cur[0] != name:
        state["phase"] = [name, t]


def phase(state: dict, now: float, stale_s: float) -> Optional[tuple]:
    """(name, seconds in phase) while the thread is active, else None. A
    phase that has not changed for `stale_s` is treated as an interrupted
    turn rather than shown as running forever."""
    name, since = (state.get("phase") or ["idle", 0])[:2]
    if name == "idle" or not since:
        return None
    age = now - since
    if age > stale_s or age < 0:
        return None
    return name, age


def rate_history(state: dict) -> List[float]:
    """Output rate of each recent request, oldest first."""
    return [out / secs for out, secs in state.get("samples") or [] if secs > 0]


def _close_response(state: dict) -> None:
    cur = state.get("cur")
    state["cur"] = None
    if not cur:
        return
    _, start, last, out = cur
    if start is None or last is None:
        return
    secs = last - start
    if secs >= SPEED_MIN_SECONDS and out >= SPEED_MIN_TOKENS:
        samples = state.setdefault("samples", [])
        samples.append([out, round(secs, 3)])
        del samples[:-SPEED_SAMPLES]


def _mark_request_start(state: dict, ts: str) -> None:
    start = epoch(ts)
    if start is not None:
        _close_response(state)
        state["req_start"] = start


def tokens_per_second(state: dict) -> Optional[float]:
    """Weighted output rate over the recent requests of one transcript,
    including the response still being written."""
    samples = list(state.get("samples") or [])
    cur = state.get("cur")
    if cur and cur[1] is not None and cur[2] is not None:
        secs = cur[2] - cur[1]
        if secs >= SPEED_MIN_SECONDS and cur[3] >= SPEED_MIN_TOKENS:
            samples.append([cur[3], secs])
    out = sum(s[0] for s in samples)
    secs = sum(s[1] for s in samples)
    return out / secs if secs > 0 and out > 0 else None


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
            if _START_RE.search(raw):
                m = _TS_RE.search(raw)
                if m:
                    ts_txt = m.group(1).decode("ascii", "replace")
                    _mark_request_start(state, ts_txt)
                    if _API_REQUEST_RE.search(raw):
                        _set_phase(state, "thinking", epoch(ts_txt))
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        req = parse_request(obj)
        if req is None:
            if obj.get("type") in ("api-request", "user"):
                _mark_request_start(state, obj.get("timestamp") or "")
                if obj.get("type") == "api-request":
                    _set_phase(state, "thinking", epoch(obj.get("timestamp") or ""))
            continue
        mid, model, usage, ts = req

        msg = obj.get("message") or {}
        if msg.get("stop_reason") == "end_turn":
            _set_phase(state, "idle", epoch(ts))
        else:
            blocks = msg.get("content") or []
            kind = blocks[-1].get("type") if blocks and isinstance(blocks[-1], dict) else ""
            if kind in _BLOCK_PHASE:
                _set_phase(state, _BLOCK_PHASE[kind], epoch(ts))
        blocks = msg.get("content") or []
        last = blocks[-1] if blocks and isinstance(blocks[-1], dict) else {}
        if last.get("type") in ("tool_use", "server_tool_use"):
            name = last.get("name", "tool")
            inp = last.get("input") or {}
            if name in _HANDBACK_TOOLS:
                # Keep the last real action; the handback itself says nothing.
                state["handed_back"] = True
            else:
                state["activity"] = summarize_tool(name, inp)
                state["handed_back"] = False
            path = isinstance(inp, dict) and (inp.get("file_path") or inp.get("notebook_path"))
            if name in _EDIT_TOOLS and path:
                files = [f for f in state.get("edited") or [] if f != path]
                state["edited"] = ([str(path)] + files)[:EDITED_FILES]
        elif last.get("type") == "text" and (last.get("text") or "").strip():
            state["activity"] = _clip(last["text"])

        # Throughput: lines of one response share an id and arrive as its
        # content blocks complete, so the last one marks the end.
        t = epoch(ts)
        cur = state.get("cur")
        if cur and cur[0] == mid:
            cur[2] = t if t is not None else cur[2]
            cur[3] = max(cur[3], usage.output)
        else:
            _close_response(state)
            state["cur"] = [mid, state.get("req_start"), t, usage.output]

        if mid and mid in recent_set:
            continue
        if mid:
            recent.append(mid)
            recent_set.add(mid)
            if len(recent) > RECENT_IDS:
                recent_set.discard(recent.pop(0))
        if not model.startswith("<"):
            hist = state.setdefault("ctx_hist", [])
            hist.append(usage.prompt)
            del hist[:-CONTEXT_HISTORY]
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
            "all": list(states.values()),
            "files": states}
