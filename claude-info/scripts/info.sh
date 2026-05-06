#!/usr/bin/env bash
# Detailed Claude Code session info, derived from the current project's
# most recently modified transcript. Used by the /claude-info slash command.

set -u

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required. Install with: brew install jq"
  exit 0
fi

cwd=$(pwd)

# Encode cwd to the projects-dir naming scheme: replace / with -, drop leading -.
# /Users/syso/dev/priv/claude-plugins -> -Users-syso-dev-priv-claude-plugins
encoded="${cwd//\//-}"
proj_dir="$HOME/.claude/projects/${encoded}"

if [ ! -d "$proj_dir" ]; then
  echo "No Claude Code session history for $cwd"
  echo "(expected at $proj_dir)"
  exit 0
fi

# Most recently modified transcript file (mtime).
transcript=$(ls -t "$proj_dir"/*.jsonl 2>/dev/null | head -1)
if [ -z "$transcript" ]; then
  echo "No transcripts found in $proj_dir"
  exit 0
fi

# Walk back from end of file to find last assistant message with a usage object.
# Transcripts can have ~10MB+ so we read in reverse.
usage_line=$(tac "$transcript" 2>/dev/null \
  | awk 'NR>2000{exit} { if ($0 ~ /"message"/ && $0 ~ /"usage"/) { print; exit } }')

if [ -z "$usage_line" ]; then
  # macOS lacks tac by default; use tail | tail-r as fallback.
  usage_line=$(tail -2000 "$transcript" \
    | awk '{ if ($0 ~ /"message"/ && $0 ~ /"usage"/) last=$0 } END{ print last }')
fi

if [ -z "$usage_line" ]; then
  echo "Transcript $transcript has no assistant messages with usage data yet."
  exit 0
fi

# Extract usage and model.
read -r model in_tok out_tok cache_create cache_read <<EOF
$(echo "$usage_line" | jq -r '
  [
    .message.model // "unknown",
    .message.usage.input_tokens // 0,
    .message.usage.output_tokens // 0,
    .message.usage.cache_creation_input_tokens // 0,
    .message.usage.cache_read_input_tokens // 0
  ] | @tsv
')
EOF

# Pick the context window size based on model id.
case "$model" in
  *"[1m]"*|*"-1m"*) window=1000000 ;;
  *) window=200000 ;;
esac

input_total=$(( in_tok + cache_create + cache_read ))
pct=$(awk -v a="$input_total" -v b="$window" 'BEGIN{ printf "%.1f", (a/b)*100 }')
remain=$(( window - input_total ))
[ "$remain" -lt 0 ] && remain=0

# Cumulative session totals (sum every assistant usage line).
totals=$(awk '
  /"message"/ && /"usage"/ {
    # Per-line jq is too slow; use grep-style extraction.
    n = match($0, /"usage":\{[^}]*\}/)
    if (n == 0) next
    blk = substr($0, RSTART, RLENGTH)
    if (match(blk, /"input_tokens":[0-9]+/))            { in_t  += substr(blk, RSTART+15, RLENGTH-15) }
    if (match(blk, /"output_tokens":[0-9]+/))           { out_t += substr(blk, RSTART+16, RLENGTH-16) }
    if (match(blk, /"cache_creation_input_tokens":[0-9]+/)) { cc += substr(blk, RSTART+30, RLENGTH-30) }
    if (match(blk, /"cache_read_input_tokens":[0-9]+/))     { cr += substr(blk, RSTART+25, RLENGTH-25) }
    msgs++
  }
  END { printf "%d %d %d %d %d", msgs, in_t, out_t, cc, cr }
' "$transcript")
read -r msgs s_in s_out s_cc s_cr <<<"$totals"

# Pretty number formatter.
fmt() { printf "%'d" "$1" 2>/dev/null || printf "%d" "$1"; }

cat <<EOF
Claude Code session info — $(date '+%Y-%m-%d %H:%M:%S')

Working directory : $cwd
Transcript        : ${transcript##*/}
Model (last call) : $model
Context window    : $(fmt "$window") tokens

Current context (last API call)
  Input (raw)         : $(fmt "$in_tok")
  Cache creation      : $(fmt "$cache_create")
  Cache read          : $(fmt "$cache_read")
  ── Input total       : $(fmt "$input_total")  (${pct}% of window)
  Output (last reply) : $(fmt "$out_tok")
  Remaining           : $(fmt "$remain") tokens

Session totals (across $msgs assistant messages)
  Input tokens        : $(fmt "$s_in")
  Output tokens       : $(fmt "$s_out")
  Cache creation      : $(fmt "$s_cc")
  Cache read          : $(fmt "$s_cr")

Tip: the persistent status line (bottom of the UI) refreshes after every
assistant turn — it's the live view; this command is the snapshot.
EOF
