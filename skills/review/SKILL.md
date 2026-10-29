---
name: review
description: Set up a code review of a GitHub PR from a theflow hub, read as a sentence - "/review <PR> [on <base>]". <PR> is a PR URL, #number, several comma-separated PRs of one stack, or nothing (pick from the PRs waiting for your review). Syncs the base, checks the PR head out detached in work/<base>/review-<n>, writes the item note (kind review), opens its herdr workspace, starts Claude there as rv-<title> and sends it the review kickoff. The review agent writes a draft review in your voice to a file built for auditing (posted text plus the evidence behind each comment and a reading guide); you audit it, edit it if needed, and say "post", and the agent posts it as one GitHub review, approving or requesting changes depending on what was found. Use when the user says "review", "code review", "CR", "look at PR ...", "re-review ...", "what's in my review queue" or "review requests".
---

# Review a PR

Usage reads as a sentence:

```
/review <PR>[,<PR>...] [on <base>]
```

| `<PR>`                        | Example                                          | Meaning                                       |
|-------------------------------|--------------------------------------------------|-----------------------------------------------|
| PR URL                        | `/review https://github.com/acme/api/pull/1519`  | review that PR (the URL fixes the base)       |
| `#<number>` / `PR <number>`   | `/review #1519 on api`                           | same, base named                              |
| several PRs of one stack      | `/review #1452,#1453,#1454 on api`               | one review item for the whole stack           |
| an existing review item       | `/review review-1519`                            | resume it (also: the PR again, once reviewed) |
| nothing                       | `/review`                                        | pick from the PRs waiting for your review     |

This is the review counterpart of `work-on`: same layout, same herdr conventions, one worktree and one Claude session
per review. The differences: the worktree is a **detached checkout of the PR head** (no local branch, so nothing can
be pushed to the author's branch by accident), the item is `review-<n>`, the short name starts with `rv-`, and the
kickoff asks for a **draft review**, never for code changes.

The point is to make the user's own review faster, not to replace it:

1. The review agent reviews the PR in the user's voice (the hub's `review/style.md`, else theflow's
   `skills/review/review-style.md`) and writes the draft to `items/<base>/drafts/review-<n>.md` in the format of
   `draft-template.md`: the verdict, body and inline comments exactly as they would be posted, each comment with the
   evidence behind it (not posted), and an audit guide.
2. The user audits the draft against the code, using the evidence and the guide, and edits it in the file or in chat
   ("drop 3", "make 1 a qq").
3. On "post", the agent re-reads the file and posts it as one GitHub review: **approve** when nothing is blocking,
   **request changes** when something is.

Every command below is relative to the hub root, so start each Bash call with `cd "$(theflow root)" &&`. It only
sets the review up: the session that runs it never reviews. When the user wants to *continue* a PR's branch (push
commits to it), that is `work-on #<n>`, not a review.

## 1. Look around

Same as `work-on` step 1: `ls -l bases; cat flow.json; theflow status; theflow gh-check`, and a gh login problem
stops here until the user has run the printed `! gh auth login ...`. If this is the first reply of a hub session,
show the welcome block from the hub rules first. After creating the worktree (step 4), `theflow gh-check
work/<base>/review-<n>` installs the gh version that repo pins, if it is missing.

## 2. Read the sentence

- `<base>` is the text after the last ` on `, if every comma-separated word there is a folder in `bases/`. A PR URL
  fixes the base by itself: the base whose `theflow repo <base>` is the URL's `owner/repo`.
- Collect every PR number. Verbs like "review", "cr", "re-review", "look at" are noise.
- A PR number without a base: try each base the user hinted at; otherwise ask with `AskUserQuestion` (header
  `Base`), one option per base on GitHub.
- A name matching an existing `work/<base>/review-*` folder, or a PR already listed in an active review note
  (`grep -l "pull/<n>" items/*/review-*.md`), is a **resume** (step 6).

**No PR given**: list the review queue, limited to the owners of the hub's bases:

```bash
for b in bases/*; do theflow repo "$(basename "$b")" 2>/dev/null; done | cut -d/ -f1 | sort -u   # the owners
gh search prs --review-requested=@me --state=open --owner <owner> \
  --json repository,number,title,author,isDraft,updatedAt --limit 30
```

Show it as a short list (repo, `#n`, title, author, draft), oldest first, with PRs of the same ticket grouped, since
those are usually a stack, only for repos that have a base. Then ask with `AskUserQuestion` (header `Review`), the
three or four most likely first, the rest via Other. Mark which ones already have an active review item.

## 3. Resolve the PR(s)

For each PR:

```bash
gh pr view <n> -R "$(theflow repo <base>)" --json number,title,url,author,state,isDraft,baseRefName,headRefName,headRefOid,body,additions,deletions,changedFiles
```

- **Merged or closed**: say so and ask before going on.
- **Draft**: mention it; go on unless the user objects.
- **Several PRs**: they must be one stack in one repo: every PR's `baseRefName` is either the default branch or the
  `headRefName` of another PR in the list. Then it is one review item, checked out at the **top** PR, and each PR is
  still reviewed against its own base. PRs that are not one stack become one review item each.
- **Stacked on a PR not in the list**: say so in one line; the review still covers only this PR's own diff
  (`origin/<baseRefName>...HEAD`).
- **Ticket**: if the title, branch or body names a ticket the hub's **Tickets** section recognises, note it for the
  kickoff; the review agent reads it itself.

Names: `<ITEM-ID>` = `review-<n>` (the top PR); short name (label, workspace, main tab `work:<short name>`, agent)
`rv-` plus 1 or 2 lowercase words from the PR title (no conventional-commit prefix, no ticket ID), at most about 20
characters, unique among active items: `rv-retry-limit`. State the plan in one line and go on unless the user objects:

```
#1519 on api → work/api/review-1519, detached at <short sha> (feat/x → main), herdr rv-retry-limit, agent rv-retry-limit
```

## 4. Create the worktree

Prepare the base as in `work-on` step 5 (owned: sync; linked: fetch only). Then fetch the PR head through the
`pull/<n>/head` ref (works for forks too) and add a detached worktree:

```bash
git -C bases/<base> fetch origin pull/<n>/head <baseRefName>
git -C bases/<base> worktree add --detach "$PWD/work/<base>/review-<n>" <headRefOid>
```

For a stack, fetch every PR's head and base the same way and check out the top PR's `headRefOid`. Check that
`git -C work/<base>/review-<n> rev-parse HEAD` equals `headRefOid`.

Write the item note `items/<base>/review-<n>.md`:

```markdown
---
item: review-1519
repo: api
kind: review
branch: feat/x                       # the PR's branch, for reference only: the worktree is detached
label: rv-retry-limit
agent: rv-retry-limit
pr: https://github.com/acme/api/pull/1519      # a stack: every PR URL, comma-separated, bottom first
author: <login>
reviewed: <headRefOid>               # the head the review started from
created: 2026-10-16
status: active
opened_by: <uuid>                    # this session's $CLAUDE_CODE_SESSION_ID
opened_by_pane: w8:p4                # this session's $HERDR_PANE_ID, for reference
---
Review of #1519 "<title>" by <login>.
```

Inside herdr, `theflow sync --start <base>/review-<n>` opens the workspace under its base and starts Claude as
`<agent>`. Then send the kickoff (below) exactly as in `work-on` step 5: write it to
`<scratchpad>/kickoff-review-<n>.md`, `herdr agent prompt <agent> "$(cat <file>)"`, no `--wait`. On `waiting on a
startup dialog`, do not answer it: tell the user, wait for idle, then send.

## 5. The review kickoff

It must stand alone; the new session knows nothing of this one. `<hub>` is the absolute path of the hub root (the
review session's working directory is the worktree), `<style>` the hub's `review/style.md` if it exists, else
theflow's `skills/review/review-style.md` (absolute paths). Fill in:

```
Code review: PR #1519 "<title>"
<url> by <author>, +120/-14 in 9 files.
Ticket: <id and how to read it>, or: no ticket.

Repo: <owner/repo>, worktree work/<base>/review-1519, detached at <headRefOid> (the PR's branch <branch>),
base origin/<baseRefName>. The PR's own diff: git diff origin/<baseRefName>...HEAD.
<stack: one line per PR, bottom first: #n <title>, diff git diff origin/<base>...<head sha>>
<anything learned in this session: draft, stacked on #n, related active items, what the user asked to focus on>

You are reviewing, not developing: do not commit, push, or change the PR on GitHub.
First run `theflow gh-check` here, and again whenever a gh command fails; if it says a login is needed, ask me to run
the command it prints, don't work around it.
1. Read the PR description, the ticket, and the existing review threads (gh pr view 1519 -R <owner/repo> --comments;
   gh api repos/<owner/repo>/pulls/1519/comments) so you don't repeat what was already said.
2. Read the diff and enough of the surrounding code to judge it: correctness, edge cases, error handling, data and
   migrations, security, concurrency, API compatibility, tests that are missing or don't test the change, and
   whether it does what the ticket asks. You may run the build, the relevant tests or the app locally if that
   settles a question; say what you ran. /code-review can give you a second pass.
3. Write a draft review in my voice, built so I can audit it fast. Read and follow <style> (my voice) and
   <theflow>/skills/review/draft-template.md (the draft's format, severities, how the verdict follows the findings,
   and how to post). Write the draft to <hub>/items/<base>/drafts/review-1519.md, then reply with its path, the
   verdict and one line per comment.
4. Wait while I audit it. I edit the file or tell you what to change in chat; the file is the source of truth.
5. Only when I say "post": re-read the file and post it as one GitHub review, exactly as draft-template.md says.
If the PR gets new commits later and I ask for a re-review: git fetch origin pull/1519/head, review the commits since
<headRefOid> (git diff <headRefOid> FETCH_HEAD), then git checkout --detach FETCH_HEAD.
Once the review is posted and no re-review is expected, offer /wrap-up here; run it only on my yes.
```

## Review style

The hub's `review/style.md` is the user's review voice; `draft-template.md` (next to this file) is the draft's format
and the posting rules. The kickoff points the review agent at both; never paste them into the kickoff. The style
grows in two ways:

- `wrap-up` asks each review agent how the user changed its draft and appends a one-line lesson under **Learned from
  edits**.
- To learn it from GitHub (the first time, then every few months): `theflow review-corpus <scratchpad>/corpus.jsonl`
  collects every review comment, review body and PR comment of the user's on PRs they reviewed or commented on in
  the owners of the hub's bases. Analyse it in code (lengths, verdict counts, prefixes, recurring topics; only the
  user's comments on other people's PRs, `own_pr: false`), read a sample of the newest comments, and write or update
  the hub's `review/style.md` (starting from theflow's starter). Keep **Learned from edits** unless a newer lesson
  contradicts an entry. It describes a real person's voice: it stays in the hub, never in theflow.

## 6. Resume

Create nothing. `theflow sync --start <base>/review-<n>` reopens the workspace and resumes the note's
`claude_session`. Send no kickoff to a resumed conversation. If the PR has new commits since the note's `reviewed`
SHA, say so (`gh pr view <n> --json headRefOid`) and give the user a one-line re-review prompt to send in that
session; the hub does not send work instructions itself. If the note has no `claude_session`, Claude starts fresh:
send the full kickoff.

## 7. Hand over and stop

Do not review in this session. Reply with:

- the PR(s) as markdown links, with author, size, draft or stacked status,
- the worktree path, the SHA it is detached at, and the base it diffs against,
- the herdr label, the note path, and the agent: running as `<agent>` with the kickoff sent; jump there with
  `herdr agent focus <agent>`. Outside herdr: `cd work/<base>/review-<n>`, run `claude`, paste the kickoff,
- where the draft will appear (`items/<base>/drafts/review-<n>.md`) and the flow: audit it, edit it, say "post",
- the kickoff prompt in its own fenced code block,
- when done: `/wrap-up review-<n>` (it never deletes the author's branch or touches the PR).
