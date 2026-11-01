# claude-info

Status line and `/claude-info` report for Claude Code: what is running, what
it costs, and what is about to cost you.

## Status line

Two lines. Anything that is normal stays quiet; segments appear when there is
something worth acting on.

```
Opus 5.5 medium  121 t/s  +Haiku 5.5 12%  claude-plugins ▸ claude-info  main ↑2 +1 ~3  #12 review  2 agents 98·131 t/s
███▌······ 35% 352k/1M  $4.12  $2.75/h  cache 3m  5h 46% full in 2h20 ↻2h59  7d 31%→72%  +1156 −23  today $34  Σ$212  1h30
```

**Line 1: what is running where**

| Segment | Meaning |
| --- | --- |
| `Opus 5.5 medium` | model, effort level, `fast` when fast mode is on, `@agent` with `--agent` |
| `121 t/s` | output tokens per second of the main thread, averaged over the last requests |
| `+Haiku 5.5 12%` | other models this session spent on (after `/model` switches or in subagents), by share of cost |
| `project ▸ subdir` | project and the subdirectory you are in; `+N` for added dirs |
| `main ↑2 ↓1 !1 +1 ~3 ?2` | branch, ahead/behind upstream, conflicts, staged, modified, untracked; `REBASE`/`MERGE`/... while one is in progress |
| `#12 review` | open PR for the branch (clickable), coloured by review state |
| `2 agents 98·131 t/s` | running subagents and their output rates; a combined `Σ` rate when there are many |

**Line 2: what it costs**

| Segment | Meaning |
| --- | --- |
| context bar | context fill, green/yellow/red at 50/80% |
| `$4.12  $2.75/h` | session cost and burn rate (after 10 minutes) |
| `cache 3m` | prompt cache expires soon; yellow under a minute |
| `cache cold: next turn ≈$1.70` | cache expired; what the next turn costs to rebuild it |
| `new model: next turn re-caches 352k ≈$1.76` | after a model switch the cache starts over, since caches are per model |
| `miss: tools changed` | a recent cache miss and its cause |
| `5h 46% full in 2h20 ↻2h59` | rate-limit window; projected at the current pace, coloured by where it is heading |
| `7d 31%→72%` | projected usage when the window resets |
| `spend 63% $314/$500` | gateway spend limit, when one applies |
| `+1156 −23` | lines changed this session |
| `today $34  Σ$212` | spend today across all projects; lifetime spend in this project |

On a narrow terminal the lowest-priority segments drop out first; model,
context and cost always stay.

## /claude-info

A full report drawn with [Rich](https://github.com/Textualize/rich):

- this session per model, with what the prompt cache saved
- main thread and per-subagent output rates and costs
- spend today, 7 days, 30 days, all time, with a 14-day trend
- spend by model and by project across all transcripts

Run it directly in a terminal for colour:

```bash
python3 -I claude-info/scripts/info.py
```

## Costs

Every request is priced with its own model, cache TTL, fast mode and
data-residency multiplier from [`pricing.json`](scripts/claude_info/pricing.json),
taken from the official pricing page. Unknown future model ids fall back to
the newest model of their family. Edit the file when prices change; cached
totals are rebuilt automatically when it does.

Claude Code logs every content block of a response with the same usage,
so requests are counted once per message id. On a subscription, the figures
are what the same usage would cost on the API, not what you pay.

## Install

```bash
git clone --recurse-submodules git@github.com:DatanoiseTV/claude-plugins.git
claude plugin marketplace add ./claude-plugins
claude plugin install claude-info@syso-local
```

Plugins can't set the status line, so add it to `~/.claude/settings.json`.
`refreshInterval` keeps time-based segments (cache countdown, rate limits)
current while the session is idle:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/claude-plugins/claude-info/scripts/statusline.sh",
    "refreshInterval": 30
  }
}
```

Requires Python 3.9+ and git. Rich is vendored as a submodule
(`git submodule update --init`); without it the report uses a plain layout.

## Configuration

Thresholds and toggles live in [`config.py`](scripts/claude_info/config.py).
Override any of them in `~/.claude/claude-info.json` (or `$CLAUDE_INFO_CONFIG`):

```json
{
  "bar_width": 12,
  "context_warn_pct": 60,
  "show": {"lifetime": false, "duration": false}
}
```

| Env var | Purpose |
| --- | --- |
| `CLAUDE_INFO_CONFIG` | config file path |
| `CLAUDE_INFO_CACHE_DIR` | scan state and totals (default `~/.claude/cache/claude-info`) |
| `CLAUDE_INFO_PROJECTS_DIR` | transcripts root (default `~/.claude/projects`) |
| `CLAUDE_INFO_PYTHON` | interpreter for the status line |
| `CLAUDE_INFO_DEBUG` | raise errors instead of printing a fallback line |
| `NO_COLOR` | disable colour |

## How it works

- `statusline.py` reads the session JSON on stdin and renders in about 60 ms.
- Transcripts are read incrementally from the last byte offset, so the
  status line only parses what was appended since the previous refresh.
- Output rate is measured per request from its `api-request` line to the
  last content block of the response.
- Lifetime totals are refreshed in a detached process at most once a minute,
  guarded by a lock file.

## Layout

```
claude-info/
├── commands/claude-info.md     /claude-info
├── scripts/
│   ├── statusline.sh           entry point for settings.json
│   ├── statusline.py, info.py, cumulative.py
│   └── claude_info/
│       ├── pricing.json        per-model rates
│       ├── pricing.py          rate lookup and cost
│       ├── usage.py            incremental transcript scanner
│       ├── totals.py           lifetime totals
│       ├── limits.py           rate-limit pacing
│       ├── gitinfo.py          git state
│       ├── statusline.py       status line segments and layout
│       ├── report.py           /claude-info report
│       ├── fmt.py, config.py
├── tests/                      python3 -m unittest (from tests/)
└── vendor/rich                 submodule
```
