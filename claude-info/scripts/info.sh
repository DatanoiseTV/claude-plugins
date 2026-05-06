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

# Cumulative session totals — proper JSON parsing so we don't miss nested
# fields (cache_read_input_tokens lives after a nested cache_creation object).
totals=$(jq -r '
  select(.type == "assistant" and .message.usage)
  | [
      .message.usage.input_tokens // 0,
      .message.usage.output_tokens // 0,
      .message.usage.cache_creation_input_tokens // 0,
      .message.usage.cache_read_input_tokens // 0
    ] | @tsv
' "$transcript" 2>/dev/null | awk -F'\t' '
  { in_t += $1; out_t += $2; cc += $3; cr += $4; msgs++ }
  END { printf "%d %d %d %d %d", msgs+0, in_t+0, out_t+0, cc+0, cr+0 }
')
read -r msgs s_in s_out s_cc s_cr <<<"$totals"

# Pretty number formatter.
fmt() { printf "%'d" "$1" 2>/dev/null || printf "%d" "$1"; }

# Run the cumulative scan (warm cache → ~50ms; cold → ~2s).
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
"$SCRIPT_DIR/cumulative.py" >/dev/null 2>&1 || true

CACHE_FILE="${HOME}/.claude/cache/claude-info-totals.json"

# This-project lifetime: encode current cwd to the projects/ naming scheme.
encoded="${cwd//\//-}"
proj_json=$(jq --arg p "$encoded" '.projects[$p] // {}' "$CACHE_FILE" 2>/dev/null || echo '{}')
read -r p_cost p_in p_cc p_cr p_out p_msgs p_sessions <<<"$(echo "$proj_json" | jq -r '
  [(.cost // 0), (.in // 0), (.cc // 0), (.cr // 0), (.out // 0), (.msgs // 0), (.sessions // 0)] | @tsv
')"

# Global lifetime aggregate.
read -r l_cost l_in l_cc l_cr l_out l_msgs l_sessions <<<"$(jq -r '
  [(.lifetime_cost_usd // 0), (.lifetime_input_tokens // 0),
   (.lifetime_cache_creation // 0), (.lifetime_cache_read // 0),
   (.lifetime_output_tokens // 0), (.message_count // 0), (.session_count // 0)]
  | @tsv' "$CACHE_FILE" 2>/dev/null || echo $'0\t0\t0\t0\t0\t0\t0')"

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

This session totals ($msgs assistant messages)
  Input tokens        : $(fmt "$s_in")
  Output tokens       : $(fmt "$s_out")
  Cache creation      : $(fmt "$s_cc")
  Cache read          : $(fmt "$s_cr")

This project lifetime ($(fmt "${p_sessions:-0}") sessions, $(fmt "${p_msgs:-0}") assistant messages)
  Input tokens        : $(fmt "${p_in:-0}")
  Output tokens       : $(fmt "${p_out:-0}")
  Cache creation      : $(fmt "${p_cc:-0}")
  Cache read          : $(fmt "${p_cr:-0}")
  Estimated cost      : \$$(printf '%.2f' "${p_cost:-0}")  (API rates × token counts;
                          may differ from your actual bill on subscription)

Across all projects ($(fmt "${l_sessions:-0}") sessions, $(fmt "${l_msgs:-0}") assistant messages)
  Estimated cost      : \$$(printf '%.2f' "${l_cost:-0}")

Top 10 projects by estimated cost:
$("$SCRIPT_DIR/cumulative.py" --by-project 2>/dev/null | head -10 | awk -F'\t' '
  { name=$8; sub(/^-/, "/", name); gsub(/-/, "/", name); printf "  %-50s  $%10.2f  %5d sessions\n", name, $1, $7 }
')
Tip: the persistent status line (bottom of the UI) refreshes after every
assistant turn — it shows your project lifetime as Σ\$X.XX next to the
session cost.
EOF
