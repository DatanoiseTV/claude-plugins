# claude-info

A Claude Code plugin that surfaces session info inside the UI.

## What it shows

A two-line status line at the bottom of the UI:

```
[Opus 4.7 [1m]] claude-plugins  main*  effort=xhigh
▓▓▓░░░░░░░░░░░░░░░░░  15%  ctx 147.2k/1.0M  out 312  cache 98%  $0.1234  3m7s
```

Line 1 — model, project dir (with relative subdir if you've cd'd elsewhere),
git branch with dirty marker, worktree, effort level, output style.

Line 2 — context-window progress bar (green → yellow → red as it fills),
tokens-in / tokens-max, last reply size, cache-hit ratio, session cost,
wall-clock duration.

## Slash command

`/claude-info` prints a snapshot pulled from the current project's most
recent transcript: model, exact token counts (input / cache-creation /
cache-read / output), context-window size, and cumulative session totals.

## Installation

```bash
claude plugin marketplace add /path/to/this/repo
claude plugin install claude-info@syso-local
```

The status line is wired into `~/.claude/settings.json` separately —
plugin `settings.json` files don't currently support the `statusLine` key.
Add this to your settings:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/claude-info/scripts/statusline.sh"
  }
}
```

## How the data flows

The status line script reads the [Claude Code session JSON](https://code.claude.com/docs/en/statusline)
on stdin: `model`, `workspace`, `cost`, `context_window.current_usage`
(per-API-call `input_tokens` + `cache_creation_input_tokens` +
`cache_read_input_tokens`), `effort.level`, etc.

The slash command's backing script (`scripts/info.sh`) walks
`~/.claude/projects/<encoded-cwd>/` to find the most recent JSONL
transcript and parses out the latest assistant message's `message.usage`
object.

## Requirements

- `jq` (e.g. `brew install jq`)
- bash 3.2+ (works on stock macOS bash)
