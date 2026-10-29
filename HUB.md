# theflow hub rules

This folder is a **theflow hub**: a workspace for running several pieces of work in parallel, each with its own
Claude session in [herdr](https://herdr.dev). It holds **bases** (the repos and folders the work happens in) and
creates **items**, one per piece of work. The hub's own `CLAUDE.md` imports these rules and adds its own (its name,
its bases, its tickets and conventions); where the two differ, the hub's own rules win.

Every command below runs from the hub root (`theflow root` prints it; start Bash calls with `cd "$(theflow root)" &&`
when your session is elsewhere). `theflow help` lists the commands.

## Items

Three kinds, all dispatched from a hub session:

- **Code item** (the default): the work changes files in a git base. It gets a git worktree of the base under
  `work/<base>/<ITEM-ID>`, its own herdr workspace nested under the base's, and its Claude runs there.
- **Talk item** (`kind: talk`): no repo changes: investigating, planning, triage, answering a question, work done in
  other tools. No worktree: its Claude runs in the base's herdr workspace (a pane of its own, or a tab with
  `"talk": "tab"` in `flow.json`), rooted at the base. In an owned base it is read-only; in a writable base (a linked
  folder of yours) it may change files there. If it turns out to need changes it cannot make, it says so and the hub
  starts a code item for them.
- **Review item** (`kind: review`, `/review`): a GitHub PR checked out **detached** in `work/<base>/review-<n>`; its
  Claude drafts a review in your voice and posts it when you say so.

## Hub sessions (every session opened in the hub root)

Every Claude session whose working directory is the hub root is a **hub session**: it only orchestrates,
dispatching each piece of work to an item and wrapping items up, and never does an item's work itself.
The SessionStart hook names its herdr tab `hub:<random slug>` (unless the user named the tab) and gives the session
the tab's name as its title, so several hubs are told apart.

### Welcome

In the hub root (not in `work/<base>/<ITEM-ID>`), your **first reply of the session must start with the welcome
below**, whatever the user's first message is. Fill the bases from `ls bases` and the Active lines from
`theflow status` (the SessionStart hook passes its output; outside herdr use `ls work/*`): blocked agents first, one
short line each, e.g. `retry-sync (blocked)`, items flagged as idle with their days, and the count against the WIP
limit from the status's last line. If the status has `closed` lines (items wrapped up since the last hub session,
from `items/inbox.md`), add one `Closed:` line with them, and name any closing that left something behind. Keep it
this short; do not add anything else before it. The examples use the hub's own bases.

After the welcome, run the `work-on` skill: it reads the request as `<what> on <base>`, asks only for what is
missing, then sets the item up.

```
👋 <hub name>: one Claude session per item. This session only dispatches and wraps up.

/work-on <what> on <base>     (I prepare the base, create the worktree and its herdr workspace, or for a talk
                                  with no code changes a pane in the base's workspace, start Claude there and
                                  send it the kickoff)
  /work-on ABC-123 on api                          a ticket (if this hub uses a tracker)
  /work-on #709 on api                             continue a GitHub PR's branch
  /work-on retry failed syncs on api               a description
  /work-on retry-sync                              resume an existing item
  /work-on adopt pane w5:p2                        give an agent no item claims a note

/review <PR> [on <base>]      (review someone's PR in its own detached worktree and session; the agent drafts it
                                  in your voice, you audit and edit, it posts on "post")

/wrap-up <item> [cancel]      (when merged, reviewed or abandoned: warn about pending work, then remove stacks,
                                  processes, herdr workspace, worktree and branches; in an item's own session,
                                  /wrap-up alone closes that session and reports back here)

Bases:  api, web                      (several: on api,web)
Active (3, limit 6): retry-sync (blocked), dark-mode (working), docs-fix (idle 9d)
Closed: export-csv (self, done)

Jump to an item's session with `herdr agent focus <short-name>`, or alt+1..9 in the sidebar's agent panel.
```

How to handle `/work-on <what> on <base>` (details in the skill):

- A ticket: fetch it as the hub's own rules say (its **Tickets** section). No such section: the hub has no tracker.
- A GitHub PR: check out the PR's existing branch in a new worktree (or resume the worktree that already has it).
- A description: if the hub's **Tickets** section asks for a ticket per piece of work, look for one or draft one
  for the user to approve; otherwise `<ITEM-ID>` is a short kebab-case slug of the description.
- An existing `<ITEM-ID>`: resume it, create nothing.
- An agent no item claims (`theflow status` lists it with its session id): adopt it as a talk item.
- `<base>` is a folder in `bases/`; several, comma-separated, mean one worktree per base with the same `<ITEM-ID>`.
- Then **stop**. Do not start the actual work in the hub session. Send the **kickoff prompt** to the new agent and
  reply with the worktree path(s) (or the talk item's pane), the herdr label(s), the agent name(s) and the kickoff.

### Kickoff prompt (every new item)

Inside herdr, `work-on` sends it to the item's agent (`herdr agent prompt <short-name> "<kickoff>"`, no `--wait`)
and also shows it in its reply, in its own fenced code block, as a fallback. Outside herdr, end the reply with it,
ready to paste into a new `claude` session in the worktree. One prompt per worktree; for a multi-base item, each
names the sibling worktrees too. It must stand alone (the new session has none of the hub's context) and include:

- the item: `<ITEM-ID>`, the ticket or PR link if there is one, and a 2–5 line summary of what needs to be done
  (with acceptance criteria if the ticket has them),
- where it runs: the base, the worktree path, the branch, and the branch it was cut from,
- for a multi-base item: the other worktrees (`work/<base>/<ITEM-ID>`) and what part of the work lives there,
- anything learned in the hub session that matters (constraints, related items, open questions),
- the gh check (bases on GitHub): run `theflow gh-check` first, and again whenever a `gh` command fails; a login
  problem goes to the user (the command it prints opens the browser), never worked around,
- how to start: read the ticket (if any), explore the relevant code, then propose a plan and wait for approval,
- the plan record: once approved, the plan (files that change, order, the tests that prove it, mapped to the
  acceptance criteria) goes under `## Plan` in the item note, by its absolute path, and is kept current. It outlives
  the conversation: a resume, a hand-over or `wrap-up` reads it there,
- the checks before it lands: the verification commands from the repo's `CLAUDE.md` and `/code-review` on the diff,
  fixing what is real; with a PR, both summed up in a short `Verification` section of its description,
- how it lands: a PR (`merge: pr`), or for a base with `merge: local`, commits on the item's branch that `wrap-up`
  fast-forwards into the base's checkout,
- the wrap-up offer: once merged (or the work needs no code), offer `/wrap-up` in that session and run it only on the
  user's yes; it closes the session and reports the outcome to the hub,
- anything the hub's own rules add to every kickoff.

A **talk item's** kickoff has the same item, link, summary, learned context and gh check, and instead of the
worktree, plan record and landing:

- where it runs: in the base `bases/<base>`; in an owned base read-only (a hook blocks edits and git changes; the hub
  may resync it), in a writable base free to change files there (commits only when the user asks),
- what to keep: decisions and conclusions go under `## Notes` in the item note (absolute path), as they are reached,
- the way out: if the work needs changes it cannot make, stop and say so, for the hub to start a code item,
- the wrap-up offer: once the question is answered or the task done, offer `/wrap-up`; run it only on the user's yes.

For a resumed item, `theflow sync --start` resumes its recorded Claude conversation and no kickoff is sent. Only
when there is no recorded conversation, send a short kickoff to pick up where it left off (the note's `## Plan` or
`## Notes` first, then `git log`, `git status` and the open PR).

### How many items at once

The limit is how many streams you can review properly, not how many agents can run. `theflow status` flags items
idle for `stale_days` and ends with the count against `wip_limit` (both in `flow.json`). Over the limit, `work-on`
still starts the item but says so and names the idle ones as candidates for `wrap-up`.

## Layout

```
<hub>/
├── CLAUDE.md               # imports these rules, then the hub's own
├── flow.json               # settings and per-base options (see below)
├── bases/                  # one per repo or folder:
│   ├── api/                #   a git clone the hub owns: read-only, reset to origin before each new item
│   └── notes -> ~/notes    #   a symlink to a folder of yours (linked): never reset; git or not
├── work/<base>/<ITEM-ID>/  # one git worktree per code or review item (none for talk items)
├── items/<base>/<ITEM-ID>.md   # one note per item; finished ones in items/<base>/done/
│                           #   items/<base>/claude-md-backlog.md: lessons waiting for the repo's CLAUDE.md
│                           #   items/inbox.md: every closing; items/.logs/: teardown logs
└── mcp/<name>.json         # optional MCP configs an item starts with (note field mcp: <name>)
```

`flow.json`, all optional but `name`:

```json
{
  "name": "work-hub",                  // the hub workspace's label
  "herdr_session": "",                 // a named herdr session ("" = the default one)
  "claude_home": "",                   // CLAUDE_CONFIG_DIR for the hub's sessions ("" = ~/.claude)
  "remote_control_prefix": "",         // prefix of unnamed Remote Control sessions under `theflow open`
  "wip_limit": 6, "stale_days": 7,
  "talk": "pane",                      // or "tab"
  "branch": {"ticket": "feat/{ticket}/{desc}", "adhoc": "feat/ad-hoc/{desc}"},
  "prefixes": {"base": "base:", "work": "work:", "talk": "talk:", "hub": "hub:"},
  "noise": [".husky/_/"],              // untracked paths that are never pending work
  "bases": {
    "api":   {"build_panes": ["npm run dev"], "worktree_links": [".env"], "merge": "pr"},
    "notes": {"merge": "local", "writable": true}
  }
}
```

Per base: `build_panes` (commands run in panes split off a new worktree workspace), `worktree_links` (untracked
files of the base's checkout, like `.env` or local data, symlinked into every worktree), `merge` (`pr`, the default
for a GitHub origin; `local`, the default otherwise: `wrap-up` fast-forwards the branch into the base's checkout),
`writable` (sessions may change the base itself: default for linked bases only).

Each item has a note, `items/<base>/<ITEM-ID>.md`, written by `work-on` (or backfilled by `theflow sync`):

```markdown
---
item: ABC-123
repo: api                                     # the base
branch: feat/ABC-123/retry-sync               # none for a talk item
kind: talk                                    # only for a talk item (review: a review item); absent for code
label: retry-sync                             # the short name: workspace label and agent name
agent: retry-sync
ticket: https://...                           # or pr: <url>; "none" if there is none
created: 2026-10-16
status: active                                # wrapping during wrap-up; done | cancelled, once in done/
claude_session: <uuid>                        # recorded by theflow sync
opened_by: <uuid>                             # the session that ran work-on: gets the FYI when the item closes
opened_by_pane: w8:p4                         # its herdr pane then, for reference
mcp: <name>                                   # optional: MCP servers this item's Claude starts with (mcp/<name>.json)
---
2 to 5 lines on what the item is.

## Plan        (code items: written by the item's agent once the user approves its plan, kept current)
## Notes       (talk items: decisions and conclusions, as they are reached)

wrap-up appends "## Outcome (<date>)", the teardown a "Teardown ..." line, and the note moves to items/<base>/done/.
```

The **note is a registry, not a memory**: facts and decisions that matter later belong in the project (its code,
docs, tickets, database), and the note only says where they went.

If you are running inside `work/<base>/<ITEM-ID>`, that worktree is your working directory. Do not touch `bases/` or
any other worktree, except to create or remove a worktree as described here. If you are a talk item's session
(working directory a base, and a note `items/<base>/<ITEM-ID>.md` with `kind: talk` whose `claude_session` is
yours), keep what is decided under `## Notes` in that note.

## Rules for owned bases (read-only)

- Never edit, commit, stage or check out a branch in an owned base. It stays on its default branch.
- The only operations allowed are syncing it with the remote default branch, and adding, listing, repairing or
  removing worktrees. A PreToolUse hook (`theflow hook protect-bases`, registered by `theflow install`) enforces
  this in every Claude session; if it blocks something legitimate, change its allowlist rather than working around
  it.
- Before creating a new worktree, sync it:
  ```bash
  B=bases/<base>; D=$(git -C $B symbolic-ref --short refs/remotes/origin/HEAD | sed 's#origin/##')
  git -C $B fetch --prune origin && git -C $B checkout $D && git -C $B reset --hard origin/$D && git -C $B clean -fd
  ```
- A **linked** base is your own checkout: only `git fetch --prune origin` before a new worktree, never a reset,
  checkout or clean. Worktrees start from `origin/<default>` (or the local default branch when there is no origin).
- New base: clone it into `bases/<name>` (owned) or `ln -s <folder> bases/<name>` (linked). `theflow sync` opens
  its `base:<name>` workspace; add an entry to `flow.json` only for the options above.

## Starting and finishing items

`work-on` creates code items with plain `git worktree add` (a new branch from the base's default, or an existing
PR's branch), writes the note, and runs `theflow sync --start <base>/<ITEM-ID>`, which opens the workspace (with the
base's `build_panes` and `worktree_links`), trusts the folder for Claude, and starts Claude there as the item's
agent with Remote Control on, its session named `work:<short name>` (`talk:` for a talk item). If a worktree for
the item already exists, it resumes it instead.

An item lives until its work is **merged** (or the question answered), then it **must** be wrapped up with the
`wrap-up` skill (`/wrap-up <item>`, or `/wrap-up <item> cancel` for abandoned work), from the hub or, as `/wrap-up`
alone, from the item's own session. It warns about pending work, stores a short outcome, and `theflow teardown`
removes what the item created: Docker compose stacks started from the worktree, processes running in it, its herdr
workspace, the worktree, its branches and stashes, the merged remote branch; then it resyncs an owned base. A talk
item holds nothing but its pane (or tab), so its wrap-up records the outcome and closes it. Every closing is
appended to `items/inbox.md`, sent as an FYI to the session that opened the item (note field `opened_by`) when it is
alive and idle, and shown as a herdr notification. Never delete a worktree whose work is not merged unless the user
explicitly says so.

## herdr conventions

The herdr sidebar mirrors the hub:

```
work-hub                    ← hub workspace (cwd: the hub root): hub sessions only, tabs hub:<slug>
base:api                    ← base workspace (cwd bases/api); each talk item adds a pane here,
                              titled talk:<short name> (agent <short name>)
  retry-sync                ← worktree workspace nested under its base, main tab work:retry-sync, agent retry-sync
base:notes
```

- a **base workspace** is `base:<name>`;
- a **worktree workspace** is the item's **short name** alone: 1 to 3 lowercase words joined by dashes, at most
  about 20 characters, naming the work (`retry-sync`; a review: `rv-retry-limit`). Never a ticket ID: the ID belongs in
  the branch. Its main tab, where the item's Claude runs, is `work:<short name>`;
- a **talk item's pane** (or tab) is `talk:<short name>`;
- a **hub tab** is `hub:<random slug>` until the user names it otherwise.

The short name is the note's `label` and also the agent's name (herdr names match `[a-z][a-z0-9_-]{0,31}`), so it
must be unique among active items: for a multi-base item, add the base to all but one (`export-csv`,
`export-csv-web`). Labels are display only: workspaces are found by path, never by label. `theflow sync` puts every
name above back on each run; a tab the user named keeps its name.

An item's Claude **session** carries the name of its tab or pane, `work:<short name>` or `talk:<short name>`, in
Claude's prompt box, `/resume` and the Remote Control list: `theflow sync` passes `--name` when it starts or resumes
it, and sends `/rename` to a running one with another name (no model turn, only while its agent is idle). A name
the user gave the session (one without a `work:`, `talk:` or `hub:` prefix) is kept. Hub sessions keep `hub:<slug>`.

- `theflow open` starts or attaches herdr (the hub's `herdr_session`), then runs `theflow sync` (with `--resume`
  when it had to start the server, so every item's Claude comes back after a reboot).
- `theflow sync` creates what is missing (the hub workspace, one per base, one per worktree), keeps the names, writes
  a note for any worktree without one, names each item's agent and records its session id in the note. Nothing is
  ever closed, so it is safe to re-run. `--start <base>/<ITEM-ID>` starts an item's Claude (`--session-id` for a new
  one, `--resume` once it has a transcript); `--resume` does that for every item without an agent.
- `theflow status` changes nothing: every item with its agent state (blocked first), agents no item claims (with
  their session id), items stuck in `status: wrapping`, extra workspaces rooted at a base (left over: close them),
  then the closings not yet shown to a hub session.
- A base workspace is for looking around and for talk items. The hub workspace is for hub sessions only.

## herdr automation

- The hub targets agents only by name (`herdr agent get|focus|prompt|read|wait <name>`), never by focus or pane
  order. Names drop when Claude exits; `theflow sync` puts them back.
- The hub prompts an item's agent for two things, the kickoff (`work-on`) and the outcome summary (`wrap-up` from
  the hub), and `theflow sync` sends it one command, `/rename <session name>`, when it is idle and its session lacks
  its item's name. It never sends work instructions, never answers an agent's approval, question or startup dialog for
  the user, and never closes a tab, pane or workspace it didn't create, except through `wrap-up`.
- An item's closing reaches the session that opened it as one FYI prompt, only when that session is alive and idle.
  The hub acknowledges it in one line and does nothing else; the durable record is `items/inbox.md`.
- `theflow sync` trusts a folder for Claude before starting a session there, so no trust dialog stops it. If an agent
  still waits on a startup dialog (`agent_not_ready`), that is the user's to answer: say so and name the workspace,
  then `herdr agent wait <name> --until idle --until done --timeout 300000` and send the kickoff.
- `wrap-up` from the hub refuses while the item's agent is `working` or `blocked`; an item wrapping itself up hands
  the teardown to a detached `theflow teardown --detach`, which waits for its turn to end.

## Housekeeping

- `git -C bases/<base> worktree list` shows every worktree of a base; `prunable` entries point to folders that are
  gone: `git -C bases/<base> worktree prune`.
- A worktree folder moved by hand: `git -C bases/<base> worktree repair <new-path>`.
- `work/<base>/` holds only active items. Anything merged is stale and must be wrapped up.

## Shell gotchas

- zsh reads `:a`, `:h`, `:s`, `:t`, ... after a variable as modifiers: `git show $REF:src/x` breaks. Write
  `"${REF}:src/x"`.
- zsh treats a word starting with `=` as a command lookup (`echo ===` fails) and an unmatched glob as an error:
  quote them.
