#!/bin/bash
# theflow's Claude Code status line (`theflow install` sets it, refreshed every second). Reads Claude's JSON on stdin.
# Left, Agnoster-like: dir > git branch (± dirty) > model > context used. Right: the hub's active items (work and talk)
# against wip_limit, the 5h and 7d rate limits used (time to reset → reset time), the clock.
# Needs jq and a powerline (Nerd) font; truecolor Gruvbox.
# so ${#var} counts characters, not bytes (macOS has no C.UTF-8, most Linux images only that)
case $OSTYPE in darwin*) export LC_ALL=en_US.UTF-8 ;; *) export LC_ALL=C.UTF-8 ;; esac
command -v jq >/dev/null || { echo "theflow status line: needs jq"; exit 0; }
input=$(cat)

# One jq call; missing values become "-" (read would collapse empty tab fields)
IFS=$'\t' read -r dir model used p5 r5 p7 r7 < <(echo "$input" | jq -r '[
  (.workspace.current_dir // .cwd // "-"),
  (.model.display_name // "-"),
  (.context_window.used_percentage // "-"),
  (.rate_limits.five_hour.used_percentage // "-"),
  (.rate_limits.five_hour.resets_at // "-"),
  (.rate_limits.seven_day.used_percentage // "-"),
  (.rate_limits.seven_day.resets_at // "-")
] | map(tostring) | join("\t")')

SEP=$'\xee\x82\xb0'   # powerline arrow U+E0B0
BRANCH=$'\xee\x82\xa0' # powerline branch U+E0A0
RST=$'\033[0m'

# Gruvbox dark (truecolor)
BG_DIR='80;73;69'      # bg2
BG_CLEAN='152;151;26'  # green
BG_DIRTY='215;153;33'  # yellow
BG_MODEL='177;98;134'  # purple
BG_OK='104;157;106'    # aqua
BG_WARN='215;153;33'   # yellow
BG_BAD='204;36;29'     # red
FG_LIGHT='235;219;178' # fg
FG_DARK='40;40;40'     # bg0
C_GREEN='184;187;38'; C_YELLOW='250;189;47'; C_RED='251;73;52'
C_DIM='168;153;132'    # gray

# ---- left side: segments ----
prev=""   # background rgb of the previous segment
left=""
left_w=0  # visible width of the left side

# seg BG FG TEXT : draw a segment, with an arrow coloured by the previous background
seg() {
  local bg=$1 fg=$2 text=$3 s
  if [ -n "$prev" ]; then
    printf -v s '\033[48;2;%sm\033[38;2;%sm%s' "$bg" "$prev" "$SEP"
    left+=$s
    left_w=$((left_w + 1))
  fi
  printf -v s '\033[48;2;%sm\033[38;2;%sm %s ' "$bg" "$fg" "$text"
  left+=$s
  left_w=$((left_w + ${#text} + 2))
  prev=$bg
}

# shorten PATH : ~ for home, every part but the last two cut to its first letter (~/P/h/work/theflow/item)
shorten() {
  local p=$1 out="" i n
  case $p in "$HOME" | "$HOME"/*) p="~${p#"$HOME"}" ;; esac
  local IFS=/
  local -a parts
  read -ra parts <<< "$p"
  n=${#parts[@]}
  for ((i = 0; i < n; i++)); do
    if [ $i -lt $((n - 2)) ] && [ -n "${parts[i]}" ]; then
      [ "${parts[i]:0:1}" = "." ] && out+="${parts[i]:0:2}" || out+="${parts[i]:0:1}"
    else
      out+="${parts[i]}"
    fi
    [ $i -lt $((n - 1)) ] && out+=/
  done
  printf '%s' "$out"
}

if [ "$dir" != "-" ]; then
  seg "$BG_DIR" "$FG_LIGHT" "$(shorten "$dir")"
  branch=$(git -C "$dir" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null \
    || git -C "$dir" --no-optional-locks rev-parse --short HEAD 2>/dev/null)
  if [ -n "$branch" ]; then
    if [ -n "$(git -C "$dir" --no-optional-locks status --porcelain 2>/dev/null | head -n 1)" ]; then
      seg "$BG_DIRTY" "$FG_DARK" "$BRANCH $branch ±"
    else
      seg "$BG_CLEAN" "$FG_DARK" "$BRANCH $branch"
    fi
  fi
fi

[ "$model" != "-" ] && seg "$BG_MODEL" "$FG_LIGHT" "${model/ context)/)}"   # "Opus 5.5 (1M context)" -> "(1M)"

if [ "$used" != "-" ]; then
  pct=$(printf '%.0f' "$used")
  if [ "$pct" -ge 80 ]; then bg=$BG_BAD; fg=$FG_LIGHT
  elif [ "$pct" -ge 50 ]; then bg=$BG_WARN; fg=$FG_DARK
  else bg=$BG_OK; fg=$FG_DARK; fi
  seg "$bg" "$fg" "ctx ${pct}%"
fi

if [ -n "$prev" ]; then   # closing arrow
  printf -v s '%s\033[38;2;%sm%s%s' "$RST" "$prev" "$SEP" "$RST"
  left+=$s
  left_w=$((left_w + 1))
fi

# ---- right side: items, limits, clock ----
rp=""; rc=""   # plain (for the width) and coloured text

# part PLAIN COLOURED : append a group, after a dim bar when it is not the first
part() {
  if [ -n "$rp" ]; then rp+=" │ "; rc+="\033[38;2;${C_DIM}m │ \033[0m"; fi
  rp+=$1; rc+=$2
}

# The hub: the nearest flow.json above the session's dir, else $FLOW_HUB (set in every pane by `theflow open`)
hub=""
d=$dir
while [ "${d:0:1}" = "/" ]; do
  [ -f "$d/flow.json" ] && { hub=$d; break; }
  [ "$d" = "/" ] && break
  d=$(dirname "$d")
done
[ -z "$hub" ] && [ -n "${FLOW_HUB:-}" ] && [ -f "$FLOW_HUB/flow.json" ] && hub=$FLOW_HUB

if [ -n "$hub" ]; then
  # Active items: notes in items/<base>/ (finished ones are in done/) with an item: key, by kind
  read -r work talk < <(awk '
    FNR == 1 { if (item && status != "done" && status != "cancelled") { if (kind == "talk") t++; else w++ }
               item = 0; kind = ""; status = ""; fm = ($0 == "---") ; next }
    fm && /^---$/ { fm = 0 }
    fm && /^item:/ { item = ($2 != "") }
    fm && /^kind:/ { kind = $2 }
    fm && /^status:/ { status = $2 }
    END { if (item && status != "done" && status != "cancelled") { if (kind == "talk") t++; else w++ }
          print w + 0, t + 0 }' "$hub"/items/*/*.md 2>/dev/null)
  limit=$(jq -r '.wip_limit // 6' "$hub/flow.json" 2>/dev/null)
  total=$(( ${work:-0} + ${talk:-0} ))
  if [ "$total" -gt "${limit:-6}" ]; then col=$C_RED; elif [ "$total" -eq "${limit:-6}" ]; then col=$C_YELLOW
  else col=$C_GREEN; fi
  text="${work:-0} work, ${talk:-0} talk of ${limit:-6}"
  part "$text" "\033[38;2;${col}m${text}\033[0m"
fi

now=$(date +%s)

# fmt_left SECS : "2h10m", "3d4h", "12m"
fmt_left() {
  local s=$1
  [ "$s" -lt 0 ] && s=0
  if [ "$s" -ge 86400 ]; then printf '%dd%dh' $((s / 86400)) $((s % 86400 / 3600))
  elif [ "$s" -ge 3600 ]; then printf '%dh%02dm' $((s / 3600)) $((s % 3600 / 60))
  else printf '%dm' $((s / 60)); fi
}

# fmt_at EPOCH FORMAT : local time via BSD or GNU date
fmt_at() { date -r "$1" "+$2" 2>/dev/null || date -d "@$1" "+$2" 2>/dev/null; }

# limit LABEL USED RESET TIMEFMT : "5h 37% 2h10m→15:40", the percentage coloured by how much is used
limit() {
  local label=$1 u=$2 r=$3 tf=$4 pct col body cbody
  [ "$u" = "-" ] && [ "$r" = "-" ] && return
  body=$label; cbody="\033[38;2;${C_DIM}m$label\033[0m"
  if [ "$u" != "-" ]; then
    pct=$(printf '%.0f' "$u")
    if [ "$pct" -ge 80 ]; then col=$C_RED; elif [ "$pct" -ge 50 ]; then col=$C_YELLOW; else col=$C_GREEN; fi
    body+=" ${pct}%"; cbody+=" \033[38;2;${col}m${pct}%\033[0m"
  fi
  if [ "$r" != "-" ]; then
    r=${r%.*}
    local t="$(fmt_left $((r - now)))→$(fmt_at "$r" "$tf")"
    body+=" $t"; cbody+=" \033[38;2;${C_DIM}m$t\033[0m"
  fi
  part "$body" "$cbody"
}

limit "5h" "$p5" "$r5" "%H:%M"
limit "7d" "$p7" "$r7" "%a %H:%M"

clock=$(date +%H:%M)
part "$clock" "\033[38;2;${FG_LIGHT}m${clock}\033[0m"

# terminal width: $COLUMNS (Claude Code sets it), then the controlling tty
cols=${COLUMNS:-}
[ -z "$cols" ] && cols=$({ stty size </dev/tty | cut -d' ' -f2; } 2>/dev/null)
[ -z "$cols" ] && cols=$({ tput cols </dev/tty; } 2>/dev/null)

printf '%s' "$left"
margin=4   # Claude Code reserves a little room at the right edge
pad=$(( ${cols:-0} - left_w - ${#rp} - margin ))
if [ "${cols:-0}" -gt 0 ] && [ "$pad" -ge 2 ]; then
  printf '%*s' "$pad" ''
else
  printf '  '
fi
printf '%b' "$rc"
echo
