#!/usr/bin/env bash
# Two-line status line for Claude Code.
# Reads session JSON on stdin, prints info to stdout.
# Schema: https://code.claude.com/docs/en/statusline

set -u

input=$(cat)

# jq is required; fall back to a one-line warning if absent so the user notices.
if ! command -v jq >/dev/null 2>&1; then
  echo "[claude-info] jq not installed — install with: brew install jq"
  exit 0
fi

# Pull every field with safe defaults. `// ""` and `// 0` keep early-session
# nulls (before the first API call, after /compact) from blowing up arithmetic.
# One field per line — bash 3.2 (macOS default) lacks mapfile.
i=0
while IFS= read -r line; do
  f[$i]="$line"
  i=$((i+1))
done < <(printf '%s' "$input" | jq -r '
  .model.display_name // "claude",
  .workspace.current_dir // .cwd // "",
  .workspace.project_dir // "",
  .context_window.context_window_size // 200000,
  .context_window.used_percentage // 0,
  .context_window.current_usage.input_tokens // 0,
  .context_window.current_usage.cache_creation_input_tokens // 0,
  .context_window.current_usage.cache_read_input_tokens // 0,
  .context_window.current_usage.output_tokens // 0,
  .cost.total_cost_usd // 0,
  .cost.total_duration_ms // 0,
  .cost.total_api_duration_ms // 0,
  .cost.total_lines_added // 0,
  .cost.total_lines_removed // 0,
  .session_id // "",
  .version // "",
  .effort.level // "",
  .output_style.name // "",
  .workspace.git_worktree // ""
')
model="${f[0]:-claude}"
dir="${f[1]:-}"
project_dir="${f[2]:-}"
window_size="${f[3]:-200000}"
used_pct="${f[4]:-0}"
in_tok="${f[5]:-0}"
cache_create="${f[6]:-0}"
cache_read="${f[7]:-0}"
out_tok="${f[8]:-0}"
cost_usd="${f[9]:-0}"
dur_ms="${f[10]:-0}"
api_ms="${f[11]:-0}"
lines_add="${f[12]:-0}"
lines_rm="${f[13]:-0}"
session_id="${f[14]:-}"
version="${f[15]:-}"
effort="${f[16]:-}"
style="${f[17]:-}"
worktree="${f[18]:-}"

# ── Colors (ANSI) ──────────────────────────────────────────────────────────
RESET=$'\033[0m'
DIM=$'\033[2m'
BOLD=$'\033[1m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
CYAN=$'\033[36m'
MAGENTA=$'\033[35m'
BLUE=$'\033[34m'

# ── Compute derived values ────────────────────────────────────────────────
# Match used_percentage: input + cache (input-only formula — see docs).
input_total=$(( in_tok + cache_create + cache_read ))

# Round used_pct to int.
pct_int=$(printf '%.0f' "$used_pct")
[ "$pct_int" -gt 100 ] && pct_int=100
[ "$pct_int" -lt 0 ] && pct_int=0

# Color for the bar based on usage tier.
if   [ "$pct_int" -lt 50 ]; then bar_color="$GREEN"
elif [ "$pct_int" -lt 80 ]; then bar_color="$YELLOW"
else                             bar_color="$RED"
fi

# 20-cell bar (each cell = 5%). Filled = ▓, empty = ░.
filled=$(( pct_int / 5 ))
[ "$filled" -gt 20 ] && filled=20
bar=""
i=0
while [ "$i" -lt 20 ]; do
  if [ "$i" -lt "$filled" ]; then bar+="▓"; else bar+="░"; fi
  i=$(( i + 1 ))
done

# Human-readable token counts (1.2k / 200k).
human_tokens() {
  local n=$1
  if   [ "$n" -ge 1000000 ]; then printf '%.1fM' "$(echo "$n / 1000000" | bc -l)"
  elif [ "$n" -ge 1000 ];    then printf '%.1fk' "$(echo "$n / 1000" | bc -l)"
  else                            printf '%d' "$n"
  fi
}
in_h=$(human_tokens "$input_total")
max_h=$(human_tokens "$window_size")
out_h=$(human_tokens "$out_tok")

# Cache-hit ratio (read / (read + create)) — 0 when no cache traffic yet.
cache_total=$(( cache_read + cache_create ))
if [ "$cache_total" -gt 0 ]; then
  cache_pct=$(( 100 * cache_read / cache_total ))
else
  cache_pct=0
fi

# Duration: ms → human (e.g. 2m4s, 47s).
fmt_duration() {
  local ms=$1 s m h
  s=$(( ms / 1000 ))
  if   [ "$s" -lt 60 ];   then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then m=$(( s / 60 )); s=$(( s % 60 )); printf '%dm%ds' "$m" "$s"
  else                          h=$(( s / 3600 )); m=$(( (s % 3600) / 60 )); printf '%dh%dm' "$h" "$m"
  fi
}
dur_h=$(fmt_duration "$dur_ms")

# Cost as $0.0123 (4 decimals if <$1, else 2).
if awk -v c="$cost_usd" 'BEGIN{exit !(c<1)}'; then
  cost_h=$(printf '$%.4f' "$cost_usd")
else
  cost_h=$(printf '$%.2f' "$cost_usd")
fi

# Per-project total cost (across every session ever run in this project_dir),
# read from cumulative.py's cache. Refreshes asynchronously when the cache
# is older than 60 seconds, so the status line itself never blocks.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
LIFETIME_CACHE="${HOME}/.claude/cache/claude-info-totals.json"
project_cost=""
if [ -f "$LIFETIME_CACHE" ] && [ -n "$project_dir" ]; then
  # Encode project_dir to the projects-dir naming scheme: / -> - .
  encoded="${project_dir//\//-}"
  project_cost=$(jq -r --arg p "$encoded" '.projects[$p].cost // empty' "$LIFETIME_CACHE" 2>/dev/null)
fi
if [ ! -f "$LIFETIME_CACHE" ] || [ -n "$(find "$LIFETIME_CACHE" -mmin +1 2>/dev/null)" ]; then
  # Detached: fully orphan the refresh so the status line returns immediately.
  ( "$SCRIPT_DIR/cumulative.py" >/dev/null 2>&1 & ) >/dev/null 2>&1
fi
if [ -n "$project_cost" ]; then
  if awk -v c="$project_cost" 'BEGIN{exit !(c<100)}'; then
    project_h=$(printf 'Σ$%.2f' "$project_cost")
  elif awk -v c="$project_cost" 'BEGIN{exit !(c<1000)}'; then
    project_h=$(printf 'Σ$%.0f' "$project_cost")
  else
    project_h=$(printf 'Σ$%.1fk' "$(echo "$project_cost / 1000" | bc -l)")
  fi
else
  project_h=""
fi

# Display: project basename, plus relative subdir if cwd is below project_dir.
# Falls back to the current dir if project_dir isn't set or cwd is elsewhere.
proj_base=""
sub_path=""
if [ -n "$project_dir" ]; then
  proj_base="${project_dir##*/}"
fi

if [ -n "$project_dir" ] && [ "$dir" = "$project_dir" ]; then
  disp_dir="$proj_base"
elif [ -n "$project_dir" ] && [ "${dir#"$project_dir"/}" != "$dir" ]; then
  sub_path="${dir#"$project_dir"/}"
  disp_dir="${proj_base} ▸ ${sub_path}"
else
  # No project_dir, or cwd is outside it — show a tilde-shortened path.
  case "$dir" in
    "$HOME") disp_dir="~" ;;
    "$HOME"/*) disp_dir="~/${dir#"$HOME"/}" ;;
    *) disp_dir="$dir" ;;
  esac
  case "$disp_dir" in
    */*/*/*) disp_dir=".../${disp_dir##*/*/}" ;;
  esac
fi

# Git branch + dirty marker (cheap enough for a status line: cwd-only).
git_seg=""
if [ -n "$dir" ] && [ -d "$dir/.git" -o -f "$dir/.git" ] 2>/dev/null; then
  branch=$(git -C "$dir" symbolic-ref --quiet --short HEAD 2>/dev/null \
           || git -C "$dir" describe --tags --exact-match 2>/dev/null \
           || git -C "$dir" rev-parse --short HEAD 2>/dev/null)
  if [ -n "$branch" ]; then
    if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
      git_seg=" ${MAGENTA} ${branch}*${RESET}"
    else
      git_seg=" ${MAGENTA} ${branch}${RESET}"
    fi
  fi
fi

# Worktree indicator (when launched with --worktree or inside a linked tree).
wt_seg=""
[ -n "$worktree" ] && wt_seg=" ${DIM}(wt:${worktree})${RESET}"

# Effort + output style indicators (only when set).
extras=""
[ -n "$effort" ] && [ "$effort" != "null" ] && extras+=" ${DIM}effort=${effort}${RESET}"
[ -n "$style" ] && [ "$style" != "null" ] && [ "$style" != "default" ] && extras+=" ${DIM}style=${style}${RESET}"

# ── Line 1: model, dir, git, extras ───────────────────────────────────────
printf '%s[%s]%s %s%s%s%s%s%s\n' \
  "$BOLD$CYAN" "$model" "$RESET" \
  "$BLUE" "$disp_dir" "$RESET" \
  "$git_seg" "$wt_seg" "$extras"

# ── Line 2: context bar + tokens + cost + duration ────────────────────────
# Tokens segment — colored by tier so it's scannable at a glance.
printf '%s%s%s %s%3d%%%s  ctx %s%s%s/%s  out %s%s%s' \
  "$bar_color" "$bar" "$RESET" \
  "$bar_color$BOLD" "$pct_int" "$RESET" \
  "$BOLD" "$in_h" "$RESET" "$max_h" \
  "$DIM" "$out_h" "$RESET"

# Cache hit ratio (only when there's been cache traffic).
if [ "$cache_total" -gt 0 ]; then
  printf '  %scache %d%%%s' "$DIM" "$cache_pct" "$RESET"
fi

printf '  %s%s%s' "$GREEN" "$cost_h" "$RESET"
[ -n "$project_h" ] && printf '  %s%s%s' "$DIM" "$project_h" "$RESET"
printf '  %s%s%s' "$DIM" "$dur_h" "$RESET"
printf '\n'
