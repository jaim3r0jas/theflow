"""SessionStart hook (registered in the hub's Claude home settings by `theflow install`, so it runs for every
session): in a hub session, name the session, greet the user and tell Claude to run the work-on skill (review for a
PR review) on its first reply, passing the live items and their agent states for the welcome. Silent anywhere else
(a worktree, a talk tab, a base's later tabs, any folder outside a hub).

Two kinds of session are hub sessions: they dispatch work, never do it.
- One in the hub root. Its herdr tab, if it still has herdr's default number, becomes hub:<random slug>.
- Inside herdr only, one in the first tab of a base's workspace, at the base's folder (a linked base can be a
  folder above the hub, like ~/Personal, so outside herdr a base folder is never a hub). Its tab becomes hub:<base>.
  A request that names no base is for that base.
The session takes the tab's name as its title (hub:<name> when the user named the tab otherwise), so several hubs
are told apart. Outside herdr it is just "hub".
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
    if not root:
        return
    hub = flowlib.Hub(root)
    base = None
    if root != cwd:   # only a base's folder, inside herdr, can still be a hub session
        base = next((name for name in hub.base_names() if hub.base_path(name) == cwd), None)
        if not base or not os.environ.get("HERDR_PANE_ID"):
            return
    herdr = hub.herdr_running()
    in_herdr = herdr and bool(os.environ.get("HERDR_PANE_ID"))
    if base and not in_herdr:
        return
    run = lambda *a: subprocess.run([sys.executable, str(SYNC), *a], capture_output=True, text=True, cwd=root)
    tab = run("--hub-tab", *(["--base", base] if base else [])).stdout.strip() if in_herdr else ""
    if base and not tab:
        return   # not the base workspace's first tab
    status = run("--status", "--mark-seen").stdout.strip() if herdr else \
        "herdr not running: list work/*/* and items/*/*.md instead"
    hub_prefix = hub.prefix["hub"]
    title = tab if tab.startswith(hub_prefix.rstrip(":")) else f"{hub_prefix}{tab}" if tab else "hub"
    bases = ", ".join(hub.base_names()) or "none yet (add one: clone into bases/<name>, or symlink a folder there)"
    name = hub.config["name"]
    if not base:
        where = f"This session was opened in the root of the theflow hub {name} ({root}): a hub session."
    else:
        where = (f"This session was opened in the first tab of base {base}'s workspace ({cwd}), in the theflow hub "
                 f"{name} ({root}): a hub session. ")
        if root not in cwd.parents:   # an owned base under bases/ loads the hub's CLAUDE.md already
            where += (f"This folder is outside the hub, so the hub's rules did not load: before your first reply, "
                      f"read {root}/CLAUDE.md and the HUB.md it imports. ")
        where += (f"Run every theflow command from the hub root (cd {root} && ...). This folder's own CLAUDE.md is "
                  f"for the item sessions of base {base}: this session only dispatches and wraps up, never does an "
                  f"item's work. A request that names no base is for base {base}.")
    print(json.dumps({
        "systemMessage": f"👋 {hub.config['name']}: /work-on <ticket | PR | item | description> on <base>, "
                         f"/review <PR> [on <base>]. Bases: {bases}",
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "sessionTitle": title,
            "additionalContext": (
                f"{where} On your first reply, whatever the user writes, invoke the `work-on` skill and follow it: "
                "read the message as `<what> on <base>` (ticket, GitHub PR, existing item or description), ask only "
                "for what is missing, then set the item up and stop. If the message asks to review a PR (or for the "
                "review queue), invoke the `review` skill instead. Live work items for the welcome's Active lines "
                f"(theflow status):\n{status}"),
        },
    }))


if __name__ == "__main__":
    main()
