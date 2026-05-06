# claude-plugins

Personal [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin
marketplace. The repo root is a marketplace (`.claude-plugin/marketplace.json`)
and each subdirectory is a plugin.

## Plugins in this marketplace

| Name          | What it does                                                                              |
| ------------- | ----------------------------------------------------------------------------------------- |
| `claude-info` | Two-line status line + `/claude-info` slash command showing model, context-window usage, tokens, cache hit ratio, cost, duration. See [`claude-info/README.md`](claude-info/README.md). |

Marketplace name: `syso-local`.

## Install

Pick whichever of these matches how you want to use it.

### As a marketplace (recommended)

Add the marketplace once, then install plugins from it.

```bash
# Local checkout — works for private repos and lets you iterate on the plugin
# files without re-publishing.
git clone git@github.com:DatanoiseTV/claude-plugins.git ~/dev/priv/claude-plugins
claude plugin marketplace add ~/dev/priv/claude-plugins

# Or if the repo is public, GitHub shorthand:
claude plugin marketplace add DatanoiseTV/claude-plugins

# Then install whichever plugin(s) you want:
claude plugin install claude-info@syso-local
```

`/plugin` inside Claude Code shows everything that's enabled.

### Wire up the status line

Plugin `settings.json` doesn't currently support the top-level `statusLine`
key, so the script ships in the plugin but the wiring lives in your user
settings. Add this to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/claude-plugins/claude-info/scripts/statusline.sh"
  }
}
```

Point it at wherever you cloned the repo. The script is run after every
assistant turn; output is two lines of color-coded text rendered at the
bottom of the Claude Code UI.

The status line is read at session start — restart Claude Code to pick it up.

## Requirements

- Claude Code ≥ 2.1
- `jq` (`brew install jq` on macOS)
- bash 3.2+ (works on stock macOS bash)

## Layout

```
claude-plugins/
├── .claude-plugin/
│   └── marketplace.json      # marketplace manifest (name: syso-local)
├── claude-info/              # plugin: status line + /claude-info
│   ├── .claude-plugin/
│   │   └── plugin.json
│   ├── commands/
│   │   └── claude-info.md
│   ├── scripts/
│   │   ├── statusline.sh
│   │   └── info.sh
│   └── README.md
└── README.md                 # this file
```

## License

MIT. See individual plugins for their own license declarations.
