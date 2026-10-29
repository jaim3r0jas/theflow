# Review draft: format and posting

The review agent writes its draft to `<hub>/items/<base>/drafts/review-<n>.md`, a file the user audits, edits
and then has posted. The draft is built so the user can check every claim fast without redoing the review: what is
posted is written exactly as it will appear on GitHub, and next to each comment is the evidence behind it, which is
not posted.

## The file

```markdown
# Review draft: #42 fix(sync): give up after the retry limit
<!-- Edit anything. Posted: Verdict, Body and each comment's text. Not posted: Evidence lines and the Audit guide.
     Delete a comment to drop it. Then tell me "post". -->

## Verdict
REQUEST_CHANGES
<!-- APPROVE when no comment is blocking; REQUEST_CHANGES when at least one is. Blocking: 1 -->

## Body
one thing blocks (1), the rest are small

## Comments

### 1 · blocking · src/sync/retry.ts:57
`attempts` starts at 1 here but the limit check uses `>`, so it retries one time more than `MAX_RETRIES`
> Evidence: `attempts = 1` at :41, `if (attempts > MAX_RETRIES)` at :57; `retry.test.ts` only checks that it stops,
> not after how many calls. Confidence: high.

### 2 · nit · src/sync/retry.ts:63
nit: `retryCountExceededError` → `RetryLimitError`, like the other errors in `errors.ts`
> Evidence: naming only. Confidence: high.

### 3 · qq · migrations/0042_add_attempts.sql:3
qq: do we need the index on `attempts`? nothing queries by it
> Evidence: grep finds no query filtering or sorting by `attempts`. Confidence: medium.

## Audit guide (not posted)
- **What it does**: stops retrying a failed sync after `MAX_RETRIES` and marks it failed; one new column.
- **Read in this order**: `retry.ts:35-70` (the loop, comment 1) → `errors.ts` → the migration (comment 3) → the
  test file.
- **Checked**: ran `retry.test.ts` (passes); the migration applies on a fresh db.
- **Not checked**: what the UI shows for a sync marked failed.
- **Already raised by others**: the log level of the retry message, so not repeated.
```

For a stack, one file with one such part per PR (`# Review draft: #1452 ...`, `# Review draft: #1453 ...`), each with
its own verdict; each part is posted as its own review on its own PR.

## Rules for the draft

- **Severity** per comment, one of:
  - `blocking`: a bug, data loss, a security hole, a broken migration or contract, a missing test of the core
    behaviour, or a question whose answer may well reveal one of those.
  - `nit`: polish (naming, small cleanups, unrelated changes). Posted with a `nit:` prefix.
  - `qq`: a genuine question that does not block. Posted with a `qq:` prefix.
- **Verdict follows the findings**: at least one `blocking` comment → `REQUEST_CHANGES`, otherwise `APPROVE` (nits
  and qqs ride along in the approval). Nothing else, unless the user asks for a plain comment review.
- **Comments are ranked** blocking first, then qq, then nit, and numbered so the user can say "drop 3".
- **Every comment points at a line in the PR's diff** (the right-hand side of `git diff <base>...HEAD`), since GitHub
  rejects inline comments elsewhere. Something about code outside the diff goes on the nearest changed line or in
  the body.
- **Evidence** is for the user, never posted: the code or command output that backs the comment, with file:line,
  what was run, and a confidence (high / medium / low). A comment you cannot back with evidence is a `qq`, or it is
  dropped.
- **The audit guide** is what lets the user review fast: what the PR does in 2 or 3 lines, the files in the order to
  read them with the line ranges that matter, what was run or checked, what was not checked, and what others
  already raised.
- Posted text follows the hub's review style (`<hub>/review/style.md`, else theflow's `review-style.md`): no
  headers or severity labels inside the posted text.

## Editing

The user edits in one of two ways, often both:

- **In the file**: they change wording, delete comments, flip the verdict. Re-read the file before doing anything
  else; it is the source of truth, not your memory of what you wrote.
- **In chat**: "drop 3", "make 1 a qq", "softer", "approve it". Apply it to the file, then show the changed parts.

Never edit the file behind the user's back once you have handed it over, other than to apply what they asked for.

## Posting

Only when the user says to post ("post", "ship it", "go"):

1. Re-read the file. If the verdict does not match the comments (`REQUEST_CHANGES` with no blocking comment left,
   or `APPROVE` with one), say so and ask; never change it on your own.
2. Check every comment's path and line against the diff again; fix or ask about any that GitHub would reject.
3. Post one review per PR, with the verdict as the event and only the posted parts (no evidence, no audit guide,
   no severity labels; the `nit:` / `qq:` prefixes stay):
   ```bash
   gh api repos/<owner>/<repo>/pulls/<n>/reviews --input <scratch>/review-<n>.json
   # {"commit_id": "<head sha reviewed>", "event": "APPROVE" | "REQUEST_CHANGES", "body": "...",
   #  "comments": [{"path": "...", "line": 88, "side": "RIGHT", "body": "..."}]}
   ```
   `REQUEST_CHANGES` needs a non-empty body: use a one-liner in the user's voice if the body is empty.
4. Reply with the review URL and the verdict. Keep the draft file: `wrap-up` uses it to learn how the user
   edits drafts, then deletes it.
