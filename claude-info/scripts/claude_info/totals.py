"""Lifetime usage across every transcript under ~/.claude/projects.

The full per-file scan state is kept in totals-state.json. A small summary
(per project, per day, per model) is written to totals.json, which is all
the status line reads. Refreshes run in a detached background process
guarded by a lock file, so the status line never waits on them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from . import config, pricing, usage

STATE_FILE = "totals-state.json"
SUMMARY_FILE = "totals.json"
LOCK_FILE = "totals.lock"


def project_key(transcript: Path, projects: Path) -> str:
    """Encoded project directory a transcript belongs to."""
    try:
        return transcript.relative_to(projects).parts[0]
    except (ValueError, IndexError):
        return "(unknown)"


def is_main_transcript(transcript: Path, projects: Path) -> bool:
    """Main session transcripts sit directly in the project directory;
    subagent transcripts are nested below a session directory."""
    try:
        return len(transcript.relative_to(projects).parts) == 2
    except ValueError:
        return False


def summarize(states: Dict[str, dict], projects: Path) -> dict:
    per_project: Dict[str, dict] = {}
    for path, st in states.items():
        p = Path(path)
        key = project_key(p, projects)
        proj = per_project.setdefault(key, {**usage.empty_totals(), "sessions": 0})
        for b in st.get("buckets", {}).values():
            usage.merge(proj, b)
        if is_main_transcript(p, projects):
            proj["sessions"] += 1

    return {
        "generated_at": int(time.time()),
        "total": usage.total(states.values()),
        "projects": per_project,
        "days": usage.by_day(states.values()),
        "models": usage.by_model(states.values()),
    }


def refresh(cache: Optional[Path] = None, projects: Optional[Path] = None) -> dict:
    cache = cache or config.cache_dir()
    projects = projects or config.projects_dir()
    prices = pricing.default()
    state_path = cache / STATE_FILE

    states = usage.load_states(state_path)
    seen: Dict[str, dict] = {}
    if projects.is_dir():
        for p in projects.rglob("*.jsonl"):
            key = str(p)
            seen[key] = usage.scan_file(p, states.get(key), prices)
    # Files that were deleted drop out of the totals.
    usage.save_states(state_path, seen)

    summary = summarize(seen, projects)
    usage.save_json(cache / SUMMARY_FILE, summary)
    return summary


def load_summary(cache: Optional[Path] = None) -> Optional[dict]:
    return usage.load_json((cache or config.cache_dir()) / SUMMARY_FILE)


def _acquire_lock(path: Path, stale_s: float) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - path.stat().st_mtime > stale_s:
            path.unlink()  # left behind by a crashed refresh
    except OSError:
        pass
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except OSError:
        return False


def refresh_locked(cfg: dict) -> None:
    cache = config.cache_dir()
    lock = cache / LOCK_FILE
    if not _acquire_lock(lock, cfg["lifetime_lock_stale_s"]):
        return
    try:
        refresh(cache)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def refresh_in_background_if_stale(cfg: dict) -> None:
    """Spawn a detached refresh when the summary is older than configured."""
    summary = config.cache_dir() / SUMMARY_FILE
    try:
        if time.time() - summary.stat().st_mtime < cfg["lifetime_refresh_s"]:
            return
    except OSError:
        pass
    if (config.cache_dir() / LOCK_FILE).exists():
        return
    scripts = Path(__file__).resolve().parent.parent
    try:
        subprocess.Popen(
            [sys.executable, "-I", str(scripts / "cumulative.py"), "--refresh"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except OSError:
        pass
