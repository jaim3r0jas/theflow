# Review style (starter)

The voice of the draft reviews. This is theflow's neutral starter: a hub replaces it with its own `review/style.md`,
learned from the user's past reviews (`theflow review-corpus`, see the review skill) and refined by `wrap-up` from
how the user edits each draft. A draft that reads like a generic AI review (headers, long bullet lists, a summary of
what the PR does, praise paragraphs) is wrong, even if the findings are right.

## Verdict and review body

- The verdict follows the findings (see `draft-template.md`): approve when nothing is blocking, with the nits and
  questions as inline comments; request changes when something is.
- The body is empty or one line: how serious the comments are, or "LGTM". Longer only for something that matters
  outside the diff (a deployment trap, a risk to keep in mind, someone else who must approve).

## Inline comments

- Short: one point per comment, one or two sentences. Say what is wrong and what you'd do instead.
- Prefixes: `nit:` for polish that does not block, `qq:` for a genuine question. No prefix for what must change.
- Ask rather than assert when you are not sure; point at the line that shows it.
- No praise filler, no restating the code, no "consider" chains.

## Learned from edits

How the user changed drafts before posting, one dated line each (appended by `wrap-up`).
