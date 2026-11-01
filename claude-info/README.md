# claude-info

Status line and `/claude-info` report for Claude Code: what is running, what
it costs, and what is about to cost you.

## Status line

A framed grid. Values stay in fixed columns, anything that is normal stays
quiet, and the graphs only move when something is actually changing.

```
╭─ Opus 5.5 medium · ⠹ thinking 12s · 108 t/s ──────────── claude-plugins ▸ claude-info · main ↑2 ~3 · #3 approved ─╮
│ ██▍······· 24% 236k/1M │ $6.40  $3.49/h            │ 5h ━━━─┃─── 38%→75% │ cache ▰▰▰▰▱ 47m                       │
│ ▁▁▁▂▂▂▃▄▅▆▇█ +2.1k/req │ today $194 ··▁···█ Σ$7.84 │ 7d ━━┃───── 22%→65% │ ⠼ 2 agents 98·131 t/s                 │
╰─ +Haiku 5.5 12% · +2900 −210 · 1h50 · statusline redesign ──────────────────────────────────────────────────────╯
```

**Top border: what is running where**

| Part | Meaning |
| --- | --- |
| `Opus 5.5 medium` | model, effort, `fast` in fast mode, `@agent` with `--agent` |
| `⠹ thinking 12s` | live phase of the main thread (`thinking`, `writing`, `tools`) and how long it has lasted; gone when the turn ends |
| `108 t/s` | output tokens per second, averaged over recent requests (`rate_spark_len` adds a per-request sparkline) |
| `project ▸ subdir` | project and subdirectory; `+N` for added dirs |
| `main ↑2 ↓1 !1 +1 ~3 ?2` | branch, ahead/behind, conflicts, staged, modified, untracked; `REBASE`/`MERGE`/... in progress |
| `#3 approved` | open PR (clickable), coloured by review state |

**Grid: what it costs**

| Column | Row 1 | Row 2 |
| --- | --- | --- |
| context | fill bar, green/yellow/red at 50/80% | context size of recent requests and average growth; drops show compactions |
| cost | session cost and burn rate | spend today with a 7-day trend, lifetime spend in this project |
| limits | 5-hour window | 7-day window |
| spend | gateway spend limit, when one applies | |
| activity | prompt cache: drain bar to expiry, `cache cold: next turn ≈$1.70`, `new model: next turn re-caches 236k ≈$1.18`, `miss: tools changed` | running subagents with their output rates |

Rate-limit gauges: `━` is usage, `┃` marks how much of the window has
passed. Usage left of the marker means you are under pace; past it, you
will run out before the reset, and the projection says when
(`5h 62% full in 1h05 ↻2h40`).

**Bottom border:** other models this session spent on (after `/model` or in
subagents), lines changed, duration, session name.

On a narrow terminal whole columns and title parts drop out by priority;
model, context and cost always stay. `"layout": "lines"` gives a plain
two-line version without frame or graphs.

### Animation

The status line is redrawn on Claude Code events and on its
`refreshInterval` timer. The spinner, phase timer and cache countdown
advance with wall time, so with `"refreshInterval": 2` they tick while the
model works, and they freeze when nothing is happening. Each refresh costs
about 70 ms of CPU; raise the interval to trade smoothness for less work.

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
`refreshInterval` drives the animation and keeps timers current while the
model works without producing events:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/claude-plugins/claude-info/scripts/statusline.sh",
    "refreshInterval": 2
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
│       ├── statusline.py       status line segments
│       ├── box.py              framed grid layout
│       ├── report.py           /claude-info report
│       ├── fmt.py, config.py
├── tests/                      python3 -m unittest (from tests/)
└── vendor/rich                 submodule
```
