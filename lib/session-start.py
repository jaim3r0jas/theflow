"""SessionStart hook (registered in the hub's .claude/settings.json by `theflow init`): in the hub root, name the
session, greet the user and tell Claude to run the work-on skill (review for a PR review) on its first reply,
passing the live items and their agent states for the welcome. Silent anywhere else (e.g. inside a worktree).

Every session in the hub root is a hub session: it dispatches work, never does it. Its herdr tab, if it still has
herdr's default number, becomes hub:<random slug>; the session takes the tab's name as its title (hub:<name> when
the user named the tab otherwise), so several hubs are told apart. Outside herdr it is just "hub".
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import flowlib

SYNC = Path(__file__).resolve().parent / "sync.py"


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    cwd = Path(event.get("cwd") or os.getcwd()).resolve()
    root = flowlib.find_root(cwd)
    if root != cwd:
        return
    hub = flowlib.Hub(root)
    run = lambda *a: subprocess.run([sys.executable, str(SYNC), *a], capture_output=True, text=True, cwd=root)
    status = run("--status", "--mark-seen").stdout.strip() if hub.herdr_running() else \
        "herdr not running: list work/*/* and items/*/*.md instead"
    tab = run("--hub-tab").stdout.strip() if os.environ.get("HERDR_PANE_ID") and hub.herdr_running() else ""
    hub_prefix = hub.prefix["hub"]
    title = tab if tab.startswith(hub_prefix.rstrip(":")) else f"{hub_prefix}{tab}" if tab else "hub"
    bases = ", ".join(hub.base_names()) or "none yet (add one: clone into bases/<name>, or symlink a folder there)"
    print(json.dumps({
        "systemMessage": f"👋 {hub.config['name']}: /work-on <ticket | PR | item | description> on <base>, "
                         f"/review <PR> [on <base>]. Bases: {bases}",
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "sessionTitle": title,
            "additionalContext": (
                f"This session was opened in the root of the theflow hub {hub.config['name']} ({root}): a hub "
                "session. On your first reply, whatever the user writes, invoke the `work-on` skill and follow it: "
                "read the message as `<what> on <base>` (ticket, GitHub PR, existing item or description), ask only "
                "for what is missing, then set the item up and stop. If the message asks to review a PR (or for the "
                "review queue), invoke the `review` skill instead. Live work items for the welcome's Active lines "
                f"(theflow status):\n{status}"),
        },
    }))


if __name__ == "__main__":
    main()
