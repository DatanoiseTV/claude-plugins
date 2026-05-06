# claude-info

A Claude Code plugin that surfaces session info inside the UI.

## What it shows

A two-line status line at the bottom of the UI:

```
[Opus 4.7 [1m]] claude-plugins  main*  effort=xhigh
▓▓▓░░░░░░░░░░░░░░░░░  15%  ctx 147.2k/1.0M  out 312  cache 98%  $0.1234  Σ$127  3m7s
```

Line 1 — model, project dir (with relative subdir if you've cd'd elsewhere),
git branch with dirty marker, worktree, effort level, output style.

Line 2 — context-window progress bar (green → yellow → red as it fills),
tokens-in / tokens-max, last reply size, cache-hit ratio, current-session
cost, **`Σ$X.XX` lifetime cost spent in this project (across every session
ever launched here)**, wall-clock duration.

The lifetime number is read from a per-project cache that
`scripts/cumulative.py` maintains. It scans every `.jsonl` transcript
under `~/.claude/projects/` once (cold ~2s for ~1GB), then incrementally
on file mtime/size changes. The status line itself only reads the cache
(~10ms) and triggers a refresh in the background when the cache is
older than a minute, so it never blocks your prompt.

## Slash command

`/claude-info` prints a full breakdown:
- current-context state (last API call) and remaining tokens
- this-session totals (input / cache-creation / cache-read / output)
- **this-project lifetime totals + estimated cost** (across every Claude
  Code session ever run in the current directory)
- global lifetime cost across all your projects
- top-10 projects ranked by spend, with session counts

## Cost estimation caveat

The lifetime cost is computed from token counts × Anthropic's published
per-token rates (snapshot embedded in `scripts/cumulative.py`, in the
`RATES` table — edit if rates change). Two reasons it'll differ from
your actual bill:

- On a Claude **subscription** (Pro / Max / Team), you're billed a flat
  monthly rate, not per token. The displayed cost is what those tokens
  *would* cost on the API, not what you actually paid.
- The 5m / 1h prompt-cache split is read from the per-message
  `cache_creation.ephemeral_*` fields when present; older transcripts
  fall back to the 5m rate.

Treat the number as a relative indicator of where your usage is
concentrated, not as an invoice.

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
