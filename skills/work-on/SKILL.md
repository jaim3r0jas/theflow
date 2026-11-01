---
name: work-on
description: Set up a theflow item from a hub, read as a sentence - "/work-on <what> on <base>". <what> is a ticket (if the hub uses a tracker), a GitHub PR (URL or #number), an existing item to resume, an agent to adopt, or a plain description of the work; <base> is one or more folders in the hub's bases/. Prepares the base, creates the worktree, its item note and its herdr workspace (a talk item, with no repo changes: only the note and a tab in the base's workspace), starts Claude there as <short-name> and sends it the kickoff prompt. Use at the start of every session opened in a hub root, or when the user says "work on ...", "start ...", "new item", "resume ...", "adopt ...", "what are we working on" or "new \"...\" on <base>".
---

# Work on an item

Usage reads as a sentence:

```
/work-on <what> on <base>[,<base>...]
```

| `<what>`                         | Example                                  | Meaning                                         |
|----------------------------------|------------------------------------------|-------------------------------------------------|
| ticket ID or URL                 | `/work-on ABC-123 on api`                | new item from the ticket (hubs with a tracker)  |
| GitHub PR URL or `#<number>`     | `/work-on #709 on api`                   | continue that PR's branch                       |
| existing item                    | `/work-on retry-sync`                    | resume it (a worktree or a talk item)           |
| an agent no item claims          | `/work-on adopt pane w5:p2`              | give it a note: it becomes a talk item          |
| a description (quotes optional)  | `/work-on retry failed syncs on api`     | new item                                        |
| any of the above, several bases  | `/work-on ABC-123 on api,web`            | one worktree per base, same `<ITEM-ID>`         |

If the message asks to **review** a PR ("review #1519", "CR", "my review queue"), use the `review` skill instead.

Runs from any session: the hub, a base or a worktree. Every command below is relative to the hub root, so start each
Bash call with `cd "$(theflow root)" &&` (from a worktree or base of the hub it finds the hub itself). It only sets
the item up: the session that runs it never does the new item's work, and a worktree session that runs it leaves its
own item untouched. The hub's `CLAUDE.md` (its own sections: **Bases**, **Tickets**, **Kickoff extras**) adds to and
wins over what follows.

## 1. Look around

```bash
ls -l bases; cat flow.json
theflow status       # active items with their agent state (blocked first), and agents no item claims
theflow gh-check     # gh runs and its login is valid (installs a missing pinned gh version itself)
```

**gh first** (any base on GitHub). Every session after this one relies on `gh` (PRs, reviews, wrap-ups). If
`theflow gh-check` exits 2 (not logged in, login expired, or an account that can't read the repo), stop and ask the
user to run the command it prints (`! gh auth login ...`, it opens the browser), then check again; never log in for
them or work around it. Exit 1 (gh doesn't run): pass on its message and stop.

The status ends with a line counting the active items against `wip_limit`, and flags items nobody touched for
`stale_days` (`[idle 9d: wrap up or cancel?]`). Starting more items than the user can review only moves the queue to
them, so when a **new** item would go over the limit, say so in the one-line plan (step 4) and name the idle items as
candidates for `/wrap-up` (or `cancel`). It is a warning, not a gate.

Outside herdr (`theflow status` says herdr is not running), list `work/*/*/` and `items/*/*.md` instead. If this is
the first reply of a hub session, show the welcome block from the hub rules (bases and the Active lines filled in;
the SessionStart hook already passed the status output).

Each base is **owned** (a git clone in `bases/`: read-only, reset to origin before each item) or **linked** (a
symlink to a folder of the user's: never reset, and writable unless `flow.json` says otherwise); a base without git
takes talk items only. `theflow doctor` lists them.

## 2. Read the sentence

Split the arguments into `<what>` and `<base>`:

- `<base>` is the text after the **last** ` on ` (or a leading `on <base>`), but only when every comma-separated word
  there is a folder in `bases/`. Otherwise ` on ` is part of the description and there is no base yet. Accept loose
  forms like "base `api`" or "in api" too.
- Classify `<what>`, first match wins:
  1. **Adopt**: the user points at an agent that `theflow status` lists as `no item` ("adopt pane w5:p2", "adopt the
     cleanup session").
  2. **Ticket**: matches the ticket reference of the hub's **Tickets** section (no such section: never).
  3. **PR**: a `github.com/.../pull/<n>` URL or `#<n>` / `PR <n>`. A URL also fixes the base (its repo).
  4. **Resume**: an existing `work/<base>/<ITEM-ID>` folder, or an active note's item ID or label in `items/<base>/`
     (not `done/`).
  5. **Description**: anything else. Extra context the user gave (notes, acceptance criteria) belongs to the item;
     keep it for the kickoff prompt.
- Leading verbs like "lets", "implement", "new", "resume" are noise.

Ask only for what is missing. If `<what>` is empty, use `AskUserQuestion` (header `Item`) with options for the kinds
this hub supports and **Resume** (list the active items).

## 3. Resolve the item

- **Adopt**: create nothing in git. Take the pane's workspace (its base), its terminal title and its session id from
  the `no item` line of `theflow status`. Write a talk note (step 5, **Talk item**) with `claude_session: <that id>`,
  a short name from the topic, and 2–5 lines from the title and the user's words, then run `theflow sync`: it names
  the agent and gives it a tab of its own (out of a split, still running). No kickoff: the conversation is already
  running. An agent in a worktree workspace needs no adopting: `theflow sync` already wrote that worktree a note.
  Reply and stop.
- **Ticket**: fetch it as the hub's **Tickets** section says. If the integration is not connected, ask the user to
  connect it and stop. `<ITEM-ID>` is the ticket ID; the branch is `flow.json` `branch.ticket` with `{ticket}` and
  `{desc}` (a 3 to 6 word lowercase dashed form of the title).
- **PR**: `gh pr view <n> -R "$(theflow repo <base>)" --json state,headRefName,baseRefName,title`. If an active
  worktree already has that branch checked out (`git -C bases/<base> worktree list`), treat it as a resume. Otherwise
  the worktree checks out the **existing** PR branch (no new branch); `<ITEM-ID>` is a short slug of the branch name.
  If the PR is merged or closed, say so and ask before going on.
- **Resume**: create nothing; jump to step 6.
- **Description**: if the hub's **Tickets** section wants every item to have a ticket, look for one that covers it
  and, failing that, draft one as the section says, show it, and create it only on the user's yes in this turn
  (`AskUserQuestion`, header `Ticket`: **Create it (Recommended)**, **Change it**, **No ticket**); with a ticket, go on
  as a **Ticket** item. Otherwise `<ITEM-ID>` is a 2 to 4 word kebab-case slug and the branch is `branch.adhoc` with
  `{desc}` the same slug or slightly longer. Confirm the slug if the description is vague.

If `work/<base>/<ITEM-ID>` already exists, treat it as a resume. If the item builds on an **open** PR, ask whether to
stack on that PR's branch or start from the default branch, and recommend stacking.

## 4. Pick the base, the kind and the names

If the sentence named no base, use `AskUserQuestion` (header `Base`, `multiSelect: true`), one option per folder in
`bases/`, the likely one first with "(Recommended)".

**Code or talk.** A **code item** changes files in a git base (code, docs, config, its `CLAUDE.md`): it gets a
worktree. A **talk item** changes no repo files through a branch: investigating, planning, triage, answering a
question, work done in other tools, or (in a writable base) small changes made right in the user's own folder. It
gets no worktree and no branch; its Claude runs in the base's herdr workspace, rooted at the base. A base without git
takes talk items only, a PR item is always code. When it could go either way, ask (`AskUserQuestion`, header `Kind`):
the likelier one first with "(Recommended)", **Code (worktree)** / **Talk (in the base)**.

Pick the item's **short name** (the note's `label`): 1 to 3 lowercase words joined by dashes, at most about 20
characters, naming the work (`retry-sync`, `dark-mode`), with no base prefix and never a ticket ID. It is the
worktree workspace's label (main tab `work:<short name>`), a talk item's tab (`talk:<short name>`) and the
agent's name (`agent`, the same string), so it must not match another active item's; for a multi-base item, add the
base to all but one (`export-csv`, `export-csv-web`).

Then state the plan in one line and go on unless the user objects:

```
ABC-123 on api → work/api/ABC-123, branch feat/ABC-123/retry-sync (from origin/main), herdr retry-sync, agent retry-sync
budget question on notes → talk: a tab talk:budget-q in base:notes (cwd bases/notes, writable), agent budget-q
```

Over the WIP limit (step 1), add: `That makes 8 active items (limit 6). Idle a week or more: docs-fix (/wrap-up
<item> [cancel]).`

## 5. Create the item

Prepare each base first. **Owned** base (never edit or commit in it):

```bash
B=bases/<base>; D=$(git -C $B symbolic-ref --short refs/remotes/origin/HEAD | sed 's#origin/##')
git -C $B fetch --prune origin && git -C $B checkout $D && git -C $B reset --hard origin/$D && git -C $B clean -fd
```

**Linked** git base: `git -C bases/<base> fetch --prune origin` only (it is the user's checkout: never reset, check
out or clean it). Start from `origin/$D`; without an origin, from the local default branch.

Then the worktree. `<start>` is `origin/$D`, the local default branch, or a stacked PR's `origin/<branch>`:

```bash
git -C bases/<base> worktree add -b <branch> "$PWD/work/<base>/<ITEM-ID>" <start>    # new branch
git -C work/<base>/<ITEM-ID> branch --unset-upstream 2>/dev/null || true          # the first push makes its own
git -C bases/<base> worktree add "$PWD/work/<base>/<ITEM-ID>" <pr-branch>           # or: an existing PR branch
theflow gh-check work/<base>/<ITEM-ID>      # bases on GitHub: a repo may pin its own gh version
```

Use plain `git worktree add`, not `herdr worktree create`: `theflow sync` opens the workspace, and only a workspace it
opens gets the base's `build_panes` (it also links the base's `worktree_links` into the worktree).

Write the item note `items/<base>/<ITEM-ID>.md` (format in the hub rules): `item`, `repo` (the base), `branch`,
`label`, `agent`, `ticket` (or the field the hub's **Tickets** section names) or `pr` (URL) if there is one,
`created` = today, `status: active`, `opened_by` and `opened_by_pane` (`echo "$CLAUDE_CODE_SESSION_ID
$HERDR_PANE_ID"`: when the item wraps itself up, the teardown sends this session an FYI with the outcome, if it is
still alive and idle), then 2 to 5 lines on what the item is. Add `mcp: <name>` only when the item needs one of the
hub's on-demand MCP configs (`mcp/<name>.json`), as the hub's rules say.

Inside herdr, open the workspace and start Claude in it:

```bash
theflow sync --start <base>/<ITEM-ID>   # opens the worktree workspace under its base with the note's label and the
                                        # base's build_panes, trusts the folder for Claude, gives the note a fresh
                                        # claude_session and starts Claude there as <agent>, Remote Control on,
                                        # its session named work:<label> (talk:<label> for a talk item)
```

**Send the kickoff** once it printed `started <agent>`. Write the kickoff prompt to a file in the scratchpad first
(it holds quotes and backticks), then:

```bash
herdr agent prompt <agent> "$(cat <scratchpad>/kickoff-<ITEM-ID>.md)"   # no --wait: the hub doesn't watch the work
```

(With `herdr_session` set in `flow.json`, every `herdr` call here is `herdr --session <name> ...`.) If it printed
`waiting on a startup dialog` instead, **do not answer it**: tell the user to answer it in the `<label>` workspace,
then `herdr agent wait <agent> --until idle --until done --timeout 300000` and send the kickoff.

**Talk item.** No worktree, no branch, nothing under `work/`. After preparing the base:

1. `theflow gh-check bases/<base>` (a base on GitHub).
2. Write the note as above, with `kind: talk` and without `branch`; the body's last line is an empty `## Notes`
   section for what the session decides.
3. `theflow sync --start <base>/<ITEM-ID>`: it opens a tab of its own in the base's workspace, rooted at the base,
   and starts Claude there as `<agent>`.
4. Send the kickoff as above, in the talk form of **Kickoff prompt** in the hub rules.

## 6. Hand over and stop

Do not start the actual work in this session. Reply with:

- the worktree path(s), or for a talk item: "talk, tab `<id>` (`talk:<short name>`) in the `base:<base>` workspace",
- the branch name(s) and what they were cut from,
- the herdr workspace label(s) and the note path(s),
- the ticket or PR as a markdown link, if there is one,
- anything worth knowing (branch far behind its base, overlapping active items, open questions),
- the agent: running as `<agent>` with the kickoff sent; jump there with `herdr agent focus <agent>`. Outside herdr:
  `cd work/<base>/<ITEM-ID>`, run `claude` there and paste the kickoff,
- the **kickoff prompt** in its own fenced code block (one per worktree), following **Kickoff prompt** in the hub
  rules and the hub's **Kickoff extras**.

**Resume**: create nothing. `theflow gh-check work/<base>/<ITEM-ID>` (the resumed session gets no kickoff, so this is
its gh check), then `theflow sync --start <base>/<ITEM-ID>` (it writes a note if the item has none): it reopens a
missing workspace, and if no agent runs there it resumes the note's `claude_session`. Send no kickoff to a resumed
conversation. If the note had no `claude_session`, Claude starts fresh: send a short kickoff to pick up where it left
off (the note's `## Plan` or `## Notes` first, then `git log`, `git status`, the open PR). A talk item resumes the
same way, in a new pane of the base's workspace.
