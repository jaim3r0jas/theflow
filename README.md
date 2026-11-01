# theflow

Run many pieces of work in parallel with [Claude Code](https://claude.com/claude-code), one session per piece of
work, without losing track of any of them.

theflow turns a folder into a **hub**. You talk to a Claude session in the hub, and it only dispatches: for each
piece of work it creates an **item** (a git worktree and branch, or for work with no code changes just a note), opens
it as a workspace in [herdr](https://herdr.dev), starts a Claude session there and hands it a self-contained kickoff
prompt. When the work is merged, one command tears everything down again (Docker stacks, processes, the workspace,
the worktree, the branches) and reports back to the hub.

```
work-hub                     ← you talk to Claude here: /work-on, /review, /wrap-up
base:api                     ← one workspace per repo (base); talk items run as tabs here
  retry-sync                 ← one workspace per code item: its own worktree, branch and Claude
  rv-retry-limit                ← a review item: a PR checked out detached, a draft review in your voice
base:web
  dark-mode
```

## What you get

- **Three skills** for Claude Code: `/work-on <what> on <base>` (ticket, PR, description or resume),
  `/review <PR>` (a draft review you audit, then post), `/wrap-up <item> [cancel]` (warns about unmerged or
  uncommitted work first, then removes everything the item created).
- **`theflow`**, a small CLI the skills use: mirrors the hub into herdr, keeps names consistent, starts and resumes
  each item's Claude (with Remote Control on, its session named after the item), inventories and tears items down,
  checks `gh`.
- **Guard rails**: bases the hub owns are read-only (a PreToolUse hook blocks edits and branch changes there), every
  item has a note with its plan and outcome, a WIP limit and idle flags keep the number of open streams honest,
  closings are logged to an inbox and sent back to the session that opened the item.

## Concepts

| | |
|---|---|
| **hub** | a folder with `flow.json`, `bases/`, `work/`, `items/` and a `CLAUDE.md` that imports [HUB.md](HUB.md) |
| **base** | a repo or folder the work happens in: a git clone the hub owns (`bases/api`, read-only, reset to origin before each item), or a symlink to a folder of yours (`bases/notes -> ~/notes`, never reset, writable; without git it takes talk items only) |
| **code item** | a git worktree `work/<base>/<ID>` on its own branch, its own herdr workspace and Claude session; lands as a GitHub PR, or (`merge: local`) fast-forwarded into the base's checkout on wrap-up |
| **talk item** | no worktree: investigating, planning, triage, work in other tools. Its Claude runs in a tab of its own in the base's workspace |
| **review item** | a GitHub PR checked out detached; its Claude drafts a review in your voice, you edit, it posts on "post" |
| **note** | `items/<base>/<ID>.md`: what the item is, its plan, its outcome. A registry, not a memory: facts belong in the project |

The full rules every hub session follows are in [HUB.md](HUB.md).

## Requirements

macOS or Linux with `git`, `python3` (3.9+), [herdr](https://herdr.dev), [Claude Code](https://claude.com/claude-code),
`jq` and `lsof`; [`gh`](https://cli.github.com) for bases on GitHub (PRs and reviews); `docker` only if your items
start compose stacks.

## Install

```bash
git clone https://github.com/jaim3r0jas/theflow ~/theflow
~/theflow/bin/theflow init ~/hub --name my-hub     # creates the hub, links the skills, puts theflow on PATH
cd ~/hub
git clone git@github.com:acme/api.git bases/api   # a base the hub owns
ln -s ~/notes bases/notes                         # a folder of yours
theflow doctor                                    # what is missing
theflow open                                      # start herdr with the hub mirrored in it
```

In herdr's hub workspace, run `claude`: the first reply shows what is active and how to start an item.

`theflow init` links the skills into the hub's Claude home (`~/.claude/skills`) and registers the base guard in its
`settings.json`; `theflow install` does only that, for an existing hub. Fill in the hub's `CLAUDE.md`: its bases, its
ticket system (if any), and anything every kickoff or wrap-up should add. The skills read it.

## Commands

```
theflow init <dir> [--name N] [--claude-home DIR] [--herdr-session S] [--no-install]
theflow install | doctor | root
theflow open [herdr args]                  start or attach herdr; resume every item after a restart
theflow sync [--start B/ID] [--resume]     mirror bases and items into herdr; start or resume an item's Claude
theflow status                             items with agent state, idle and stuck ones, closings, WIP count
theflow teardown --check|--run|--detach B/ID [--merge] [--discard] [--cancelled] ...
theflow scan <path>                        herdr workspaces, compose projects and processes of a worktree
theflow gh-check [dir]                     gh installed (a repo's pinned version too) and logged in
theflow repo <base> | trust <dir>
theflow review-corpus <out.jsonl>          your past review comments, to learn your review style
```

## flow.json

```json
{
  "name": "work-hub",
  "herdr_session": "",
  "claude_home": "",
  "remote_control_prefix": "",
  "wip_limit": 6,
  "stale_days": 7,
  "branch": {"ticket": "feat/{ticket}/{desc}", "adhoc": "feat/ad-hoc/{desc}"},
  "prefixes": {"base": "base:", "work": "work:", "talk": "talk:", "hub": "hub:"},
  "noise": [".husky/_/"],
  "bases": {
    "api": {"build_panes": ["npm run dev"], "worktree_links": [".env"], "merge": "pr"},
    "notes": {"merge": "local", "writable": true}
  }
}
```

Everything but `name` is optional. Per base: `build_panes` run in panes next to each new worktree, `worktree_links`
are untracked files of the base (`.env`, local data) symlinked into every worktree, `merge` is `pr` (default for a
GitHub origin) or `local`, `writable` lets sessions change the base itself (default: linked folders only).

## Several hubs on one machine

Each hub can have its own herdr session and its own Claude home, so a work hub and a personal hub never see each
other's sessions, logins, memory or MCP servers:

```bash
theflow init ~/work-hub --name work-hub
theflow init ~/Personal/hub --name personal-hub --herdr-session personal --claude-home ~/Personal/.claude-home
```

`theflow open` in a hub starts its herdr session with `CLAUDE_CONFIG_DIR` set for every pane (and, with
`remote_control_prefix`, a Remote Control name prefix for unnamed sessions, such as the hub's own; an item's session
is named `work:<short name>` or `talk:<short name>`). Run `theflow install` in each hub: the
skills and the guard go into that hub's Claude home.

## Per-hub extras

theflow stays generic; a hub adds its own rules to its `CLAUDE.md` (see [templates/CLAUDE.md](templates/CLAUDE.md)):
how to read and create tickets, branch conventions, what a kickoff should add, where follow-ups go. A hub can keep
its own skills next to theflow's, its own MCP configs in `mcp/<name>.json` (an item asks for one with `mcp: <name>`
in its note), and its own review voice in `review/style.md`, learned from your past reviews with
`theflow review-corpus` (the review skill explains how).

## License

[MIT](LICENSE)
