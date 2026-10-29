#!/usr/bin/env bash
# theflow open [herdr args]: start or attach the hub's herdr session with every base and item as a workspace.
# When it has to start the server, every pane inherits the hub's environment (FLOW_HUB, and CLAUDE_CONFIG_DIR and
# the Remote Control name prefix when flow.json sets them), and every item's Claude is resumed once it is up.
set -euo pipefail
LIB=$(cd "$(dirname "$0")" && pwd -P)
eval "$(python3 "$LIB/admin.py" env)"
cd "$FLOW_HUB"

# `herdr status server` exits 0 even when the server is down: read its status line instead.
running() { "${HERDR[@]}" status server 2>/dev/null | grep -q '^status: running'; }

if running; then
  python3 "$LIB/sync.py"
else
  # herdr starts its server on attach; sync as soon as it is up.
  ( for _ in $(seq 1 30); do
      running && exec python3 "$LIB/sync.py" --resume >/dev/null 2>&1
      sleep 0.5
    done ) &
fi

exec "${HERDR[@]}" "$@"
