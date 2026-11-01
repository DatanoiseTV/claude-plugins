#!/usr/bin/env bash
# Status line for Claude Code. Thin wrapper so the settings.json entry stays
# stable; the implementation lives in claude_info/statusline.py.
# Set CLAUDE_INFO_PYTHON to use a specific interpreter (3.9+).
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${CLAUDE_INFO_PYTHON:-python3}"
if ! command -v "$py" >/dev/null 2>&1; then
  echo "claude-info: python3 not found (set CLAUDE_INFO_PYTHON)"
  exit 0
fi
exec "$py" -I "$dir/statusline.py"
