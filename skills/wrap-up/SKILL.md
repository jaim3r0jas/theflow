---
name: wrap-up
description: Wrap up a theflow item when it is done (merged) or cancelled - the counterpart of work-on. Read as a sentence - "/wrap-up <item>" or "/wrap-up <item> cancel"; inside an item's own session, "/wrap-up" alone wraps up that item (the session closes itself at the end). <item> is an item ID, a short name, a branch, a PR (URL or #number) or a worktree path. Checks for pending work and warns before anything is lost, offers to merge a local-merge base's branch, stores the outcome and lessons, then `theflow teardown` removes everything the item created - Docker stacks started from the worktree, processes running in it, its herdr workspace, the git worktree and folder, its local (and merged remote) branches, its stashes - resyncs the base and reports the closing to items/inbox.md and to the session that opened the item. Use when the user says "wrap up", "remove", "clean up", "close", "finish", "we're done with" or "cancel" an item.
---

# Wrap up an item

Usage reads as a sentence:

```
/wrap-up <item>            done: the work is merged (or never needed code)
/wrap-up <item> cancel     abandoned: throw the work away
/wrap-up [cancel]          inside an item's own session: wrap up this item (self mode)
```

`<item>` is anything that points at one item: `retry-sync`, `api/ABC-123`, the branch `feat/ad-hoc/retry-sync`,
`#709`, a PR URL, or `work/api/retry-sync`. Several items, comma-separated, are wrapped up one after the other.

Every command below is relative to the hub root, so start each Bash call with `cd "$(theflow root)" &&`. The hub's
`CLAUDE.md` (its **Wrap-up extras**) adds to and wins over what follows.

The goal is that **nothing the item created is left behind**: no containers, volumes, networks or images, no
processes, no herdr workspace, no worktree folder, no local branches or stashes, and no remote branch once it is
merged. The only things kept on purpose are listed at the end, plus the item's note, which keeps a short outcome in
`items/<base>/done/`.

**Two modes**, same steps, one difference at the end:

- **Self mode**: this session's working directory is inside `work/<base>/<ITEM-ID>`, or this session is a talk
  item's (a note `items/<base>/<ITEM-ID>.md` with `kind: talk` whose `claude_session` is `$CLAUDE_CODE_SESSION_ID`),
  and the user names no other item (or names this one). The agent that did the work writes the outcome itself, and
  the teardown runs **detached** after this session's turn ends, because closing the workspace (or the talk item's
  pane) ends this session. Only this item: a sibling with the same `<ITEM-ID>` under another base has its own session.
- **Hub mode**: any other session wraps up an item it does not run in. It asks the item's agent for the outcome, and
  runs the teardown in the foreground.

`theflow teardown` does the inventory and the teardown in both modes (`--check`, `--run`, `--detach`); this skill
does the judgment: matching the item, what counts as lost work, asking the user, the outcome and the lessons.

## 1. Find the item

**Self mode**: `<base>/<ITEM-ID>` comes from the working directory (`work/<base>/<ITEM-ID>`), or for a talk item
from its note (`grep -l "claude_session: $CLAUDE_CODE_SESSION_ID" items/*/*.md`).

**Hub mode**:

```bash
for d in work/*/*/; do d=${d%/}; echo "${d#work/}  $(git -C "$d" branch --show-current)"; done
grep -l '^kind: talk' items/*/*.md        # talk items: no worktree, matched by their note's item and label
```

Match `<item>` against the folder names, the notes' `label`, the checked-out branches, and (for a PR) the PR's
`headRefName` (`gh pr view <n> -R "$(theflow repo <base>)" --json headRefName`); a review item is detached, so match
its PR through the `pr:` field of `items/*/review-*.md`. If nothing is given, ask with `AskUserQuestion` (header
`Item`), listing the active items. If the same `<ITEM-ID>` exists under several bases, say so and wrap up all of
them unless the user names one.

## 2. Inventory (read-only)

```bash
theflow gh-check                            # first, for a base on GitHub: the verdict below depends on gh
theflow teardown --check <base>/<ITEM-ID>
```

With a broken or expired gh, a merged PR is invisible and the verdict is wrong. If `theflow gh-check` doesn't print
`gh ok`, stop before `--check`: pass on its message, and for a login problem ask the user to run the command it
prints, then check again.

The check prints the head, the agent's state, every PR of the item's branches, the branches (which exist on origin,
which are already in the default branch), the herdr workspaces, Docker compose projects and processes attributed to
the worktree by path, noise, sibling worktrees with the same ID, and then `nothing pending` or `PENDING` with one
line per reason:

| Reason                                                                 | Why it matters                             |
|------------------------------------------------------------------------|--------------------------------------------|
| uncommitted changes, or untracked files other than `noise`             | lost with the folder                       |
| commits not pushed, or a branch with commits but no remote branch      | lost with the branch                       |
| PR open or draft                                                       | not merged yet                             |
| PR closed without merge (with commits on the branch)                   | work may have been dropped by accident     |
| commits on the branch but no PR at all                                 | never reviewed or merged                   |
| (`merge: local`) commits not in the base's default branch              | not merged yet: `--merge` lands them       |
| local branch differs from the merged PR's `headRefOid`                 | commits added after the merge              |
| stashes on the item's branches                                         | lost when dropped                          |
| open PRs stacked on this branch                                        | their base would disappear                 |
| more `claude` processes in the worktree than herdr agents              | a session is still open there              |

A merged PR counts by its `headRefOid` (squash merges too); a branch already in the default branch counts as merged.
For a **review item** the PR is not ours; pending is only uncommitted changes, local commits on the detached HEAD,
and an open PR with no review from the user (the draft was never posted).

**Hub mode**: if the agent is `working` or `blocked`, **stop here** and say so. The wrap-up waits until it is `idle`
or `done`, or until the user says to kill it. Never answer a blocked agent's dialog for the user.

- **Nothing pending**: go on without asking. The user asked to wrap it up.
- **Not merged into a `merge: local` base** (and not a cancel): ask (`AskUserQuestion`, header `Merge`): **Merge into
  <default> (Recommended)** when the base's checkout is clean and on its default branch (`--merge`: a fast-forward;
  if it can't fast-forward, rebase the branch on the default branch in the worktree first), **Keep it** (stop, nothing
  removed), **Discard**. Pushing the default branch is the user's call, never part of this.
- **Something else pending**: stop and show the exact list, with a short reading of it where the note explains it.
  Ask with `AskUserQuestion` (header `Pending`): "Discard and wrap up" / "Keep it". With `cancel`: "Discard
  everything (and close the PR?)". For a branch on origin that was never merged through a PR, offer "Discard, delete
  remote too" / "Discard, keep remote". Never pick for the user, and never go on with pending work without an
  explicit yes in this turn. The yes becomes the teardown's options (step 4).

## 3. Outcome and lessons

Take the outcome from the user's message if they gave one. Otherwise (skip on cancel):

- **Self mode**: write it yourself from this conversation, the note's `## Plan` (a talk item: `## Notes`) and the PR.
- **Hub mode**, inside herdr with the agent `idle` or `done`: ask the item's session. `idle` doesn't mean its input
  is empty: first read its last lines (`herdr agent read <agent> --lines 10`), and if the `❯` line holds text the user
  is typing, don't prompt (it would be appended to theirs): ask the user for the outcome instead.

  ```bash
  herdr agent prompt <agent> "We are wrapping up this item. Reply in at most 3 lines, with no secrets, credentials \
  or personal data: what was built or decided (PR links welcome), and any open follow-ups. Then one more line \
  starting 'Lessons:': what you got wrong more than once in this repo, or what I had to correct more than once \
  (commands, conventions, gotchas), each as a general rule for the repo's CLAUDE.md, or 'Lessons: none'. Do not run \
  any tools." --wait --timeout 120000
  herdr agent read <agent> --source recent-unwrapped --lines 200   # the reply follows the last ⏺
  ```

  A review item asks instead for "... at most 3 lines: the verdict you posted (or that it was not posted), the main
  findings, and whether a re-review is expected. Then one more line starting 'Style:': how the user changed your
  draft before it was posted (tone, length, comments dropped or added), or 'Style: no changes'. Do not run any
  tools." No agent, or the wait fails: ask the user for a one-line outcome (or none).

The same format either way: at most 3 lines (what was built or decided, PR links, open follow-ups), and the lessons.
Facts and decisions that matter later belong in the project, not in the note: if some only live in this
conversation, say where they should go.

For a review item, turn a `Style:` line that reports real changes into one general lesson (not about this PR's
code); once confirmed, append it as a bullet with the date under **Learned from edits** in the hub's
`review/style.md` (create it from theflow's `skills/review/review-style.md` if missing), unless it already says so.

For any other item, turn real lessons into proposed `CLAUDE.md` rules for that repo: general (not about this item's
code), one line each, and not already said by the repo's `CLAUDE.md` files or by `items/<base>/claude-md-backlog.md`.
Lessons about the hub itself (its scripts, skills, the shell) go to the hub's `CLAUDE.md` instead.

Follow-ups that matter and have no home yet: offer one, as the hub's **Wrap-up extras** say (a task list, a ticket
drafted for approval). Never let them live only in the done note.

Show the outcome, the lessons and the follow-up proposal together and ask once (`AskUserQuestion`, header
`Outcome`). Store only what the user confirms:

- the note: append `## Outcome (<date>)` with the outcome, set `status: wrapping` (the teardown sets `done` or
  `cancelled` and moves the note). For cancel, a one-line reason if the user gave one. `## Plan` stays.
- each lesson as `- <date> (<ITEM-ID>): <rule>` in `items/<base>/claude-md-backlog.md` (create it with a
  `# CLAUDE.md backlog: <base>` heading). It goes into the repo as a small item of its own (`/work-on`).

## 4. Tear down

Options, only from the user's yes in step 2: `--merge` (fast-forward the base's checkout first), `--discard`
(pending work may go), `--delete-remote` (a remote branch not merged through a PR), `--close-pr` (cancel: close the
open PR and delete its branch), `--drop-stash <sha>` (repeatable), `--cancelled` (the note ends as `cancelled`).

**Hub mode**: `theflow teardown --run <base>/<ITEM-ID> [options]`

**Self mode**: this is the last tool call of the session.

```bash
cd "$(theflow root)" && theflow teardown --detach <base>/<ITEM-ID> [options]
```

Then reply in at most 3 lines (the outcome, that the closing goes to `items/inbox.md` and to the session that opened
the item, and the log path it printed) and **stop**: no more tool calls. The teardown waits until this session is
idle, then closes its workspace, which ends it.

A **talk item** has no worktree, so `--check` always finds nothing pending and the teardown only closes its agent's
tab in the base workspace (in self mode, once this turn ends), then records the closing like any item.

What the teardown does, in this order, refusing to start when the inventory finds pending work without `--discard`,
the agent is working or blocked (other than the self-mode session), or the note has no `## Outcome`:

1. `--merge`: `git merge --ff-only <branch>` in the base's checkout, only when it is clean and on its default branch.
2. Docker: `docker compose -p <project> down --volumes --remove-orphans` for every project started from the
   worktree, and `docker rmi` of the images named `<project>-*` only (never `--rmi local`: it removes tagged shared
   images other stacks need).
3. herdr: closes the item's workspaces, which ends its shells and agent.
4. Processes still in the worktree: SIGTERM, then SIGKILL. Never editors or a `claude` outside herdr: those are
   reported for the user to close.
5. `git worktree remove` (`--force` only for noise, linked files or `--discard`), `branch -D` of every local branch
   of the item, `worktree prune`; removes a folder left behind.
6. Stashes given with `--drop-stash`, by SHA (the stash stack is shared with every worktree). A review item's draft
   `items/<base>/drafts/review-<n>.md` is deleted.
7. Remote branches, never for a review item: deleted when the PR is merged (or `--delete-remote`) and no open PR is
   stacked on them; `--close-pr` closes the open PR with `--delete-branch`. Others are kept and reported.
8. The base: an owned one is reset to `origin/<default>`; a linked one is only fetched.
9. Verifies that nothing is left and reports: a `Teardown ...` line in the note, `status: done|cancelled` and the
   note moved to `items/<base>/done/`; an entry in `items/inbox.md`; an FYI prompt to the session that opened the
   item when it is alive, idle and not the one running this; a herdr notification. A failed teardown keeps the note
   in place with `status: wrapping`, and `theflow status` flags it. A detached run logs to
   `items/.logs/<base>-<ITEM-ID>-teardown.log`.

## 5. Report (hub mode)

The teardown prints the inbox entry. Reply with one short block per item:

- the verdict (merged / landed locally / ops-only / discarded on request / review posted or not) with the PR link,
- the outcome line and where the note went,
- what was removed, and anything left on purpose or that needs the user: editor windows to close, a remote branch
  kept, an open PR not closed, a default branch to push,
- the remaining active items for that base, and the count against the WIP limit (last line of `theflow status`),
- if `items/<base>/claude-md-backlog.md` has entries: how many lessons wait for that repo's CLAUDE.md.

Kept on purpose: shared Docker images, build daemons shared across worktrees, and this item's Claude session history
in the Claude home's `projects/`. If that history has a `memory/` folder with entries, say so: the user may want to
move useful ones elsewhere before forgetting about them.
