#!/usr/bin/env python3
"""theflow review-corpus: collect your past PR review comments, one JSON object per line.

Every PR you reviewed or commented on (gh search) in the GitHub owners of the hub's bases (or --owner, repeatable),
then your inline review comments, review bodies (with their verdict) and PR conversation comments on it. Feeds the
hub's review style guide (review/style.md, see the review skill).

Usage: theflow review-corpus <out.jsonl> [--since YYYY-MM-DD] [--owner ORG ...]
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import flowlib


def gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        print(f"! gh {' '.join(args[:3])}: {out.stderr.strip()[:200]}", file=sys.stderr)
        return []
    return json.loads(out.stdout or "[]")


def api(path):
    """All pages of a list endpoint (--slurp gives one list per page)."""
    pages = gh("api", "--paginate", "--slurp", path)
    return [x for page in pages for x in page]


def prs(since, owner):
    found = {}
    for flag in ("--reviewed-by=@me", "--commenter=@me"):
        query = ["search", "prs", flag, "--owner", owner, "--limit", "1000",
                 "--json", "repository,number,title,author,createdAt,url"]
        if since:
            query += ["--created", f">={since}"]
        for pr in gh(*query):
            found[(pr["repository"]["nameWithOwner"], pr["number"])] = pr
    return list(found.values())


def comments(pr, me):
    repo, n = pr["repository"]["nameWithOwner"], pr["number"]
    base = {"repo": repo, "pr": n, "pr_title": pr["title"], "pr_author": pr["author"]["login"],
            "own_pr": pr["author"]["login"] == me}
    rows = []
    for c in api(f"repos/{repo}/pulls/{n}/comments"):
        if c["user"]["login"] == me:
            rows.append({**base, "kind": "inline", "path": c["path"], "reply": bool(c.get("in_reply_to_id")),
                         "diff_hunk": c["diff_hunk"][-400:], "body": c["body"], "at": c["created_at"]})
    for r in api(f"repos/{repo}/pulls/{n}/reviews"):
        if r["user"]["login"] == me:
            rows.append({**base, "kind": "review", "state": r["state"], "body": r["body"] or "",
                         "at": r.get("submitted_at")})
    for c in api(f"repos/{repo}/issues/{n}/comments"):
        if c["user"]["login"] == me:
            rows.append({**base, "kind": "conversation", "body": c["body"], "at": c["created_at"]})
    return rows


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        sys.exit(__doc__.strip().splitlines()[-1])
    since = args[args.index("--since") + 1] if "--since" in args else None
    owners = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--owner"]
    if not owners:
        hub = flowlib.Hub()
        owners = sorted({r.split("/")[0] for r in map(hub.github_repo, hub.base_names()) if r})
    me = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True).stdout.strip()
    found = [pr for owner in owners for pr in prs(since, owner)]
    print(f"{len(found)} PRs reviewed or commented on by {me}", file=sys.stderr)
    with ThreadPoolExecutor(8) as pool, open(args[0], "w") as out:
        for rows in pool.map(lambda pr: comments(pr, me), found):
            for row in rows:
                out.write(json.dumps(row) + "\n")
    print(f"wrote {args[0]}", file=sys.stderr)


if __name__ == "__main__":
    main()
