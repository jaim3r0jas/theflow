"""PreToolUse hook: keep the bases a hub owns read-only (a git clone directly in <hub>/bases/, next to a flow.json).

Registered in the Claude home's settings.json by `theflow install`, so it runs in every session (hub, bases,
worktrees) and covers every hub on the machine. A linked base (a symlink to a folder of yours) is not protected:
its real path lies outside bases/. It denies:
- Edit/Write/NotebookEdit of a file in an owned base,
- a git command run on one (by -C or by the working directory, `cd` included) that is not on the allowlist:
  reading, the sync (fetch, checkout <default>, reset --hard origin/<default>, clean), worktree, and what wrap-up
  needs (branch -d/-D of a non-default branch, push --delete, stash list/show/drop),
- shell writes into one (redirects, rm, mv, cp, touch, tee, sed -i, ...).
Best effort: anything it cannot parse is allowed (fail open), never blocked by mistake.
"""
import json
import os
import re
import shlex
import subprocess
import sys

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
READ_ONLY = {
    "status", "log", "show", "diff", "rev-parse", "rev-list", "ls-files", "ls-tree", "ls-remote", "grep",
    "cat-file", "blame", "describe", "merge-base", "for-each-ref", "show-ref", "shortlog", "reflog", "help",
    "version", "count-objects", "check-ignore", "name-rev", "whatchanged", "range-diff", "cherry", "var",
    "show-branch", "diff-tree", "diff-index", "diff-files", "check-ref-format", "verify-commit", "fsck",
}
SYNC = {"fetch", "worktree", "clean", "gc", "prune", "maintenance"}
WRITERS = {"rm", "mv", "cp", "touch", "tee", "truncate", "mkdir", "rmdir", "ln", "chmod", "install", "rsync"}


def base_of(path):
    """The owned base a path is in (<hub>/bases/<name>, a real folder, with <hub>/flow.json), or None."""
    real = os.path.realpath(path)
    parts = real.split(os.sep)
    for i in range(len(parts) - 2, 0, -1):
        if parts[i] == "bases":
            hub = os.sep.join(parts[:i]) or os.sep
            base = os.path.join(hub, "bases", parts[i + 1])
            if os.path.isfile(os.path.join(hub, "flow.json")) and not os.path.islink(base) \
                    and not writable(hub, parts[i + 1]):
                return base
    return None


def writable(hub, name):
    """flow.json can open an owned base for writing ("bases": {"<name>": {"writable": true}})."""
    try:
        with open(os.path.join(hub, "flow.json")) as f:
            return json.load(f).get("bases", {}).get(name, {}).get("writable") is True
    except (OSError, ValueError, AttributeError):
        return False


def default_branch(base):
    out = subprocess.run(["git", "-C", base, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip().removeprefix("origin/") or "main"


def subst(word, env):
    """Expand $VAR and ${VAR} from assignments seen so far; unknown ones (e.g. from $(...)) stay as they are."""
    return re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m.group(1)) or m.group(0), word)


def expand(word, env, cwd):
    return os.path.join(cwd, os.path.expanduser(subst(word, env)))


def git_verdict(args, base):
    """None if this git subcommand (args after the global options) may run on a base, else the reason."""
    if not args:
        return None
    sub, rest = args[0], args[1:]
    flags = [a for a in rest if a.startswith("-")]
    names = [a for a in rest if not a.startswith("-")]
    default = default_branch(base)
    if sub in READ_ONLY or sub in SYNC:
        return None
    if sub == "symbolic-ref" and len(names) <= 1:
        return None
    if sub == "checkout" and names == [default]:
        return None
    if sub == "reset" and "--hard" in flags and names == [f"origin/{default}"]:
        return None
    if sub == "branch":
        if {"-d", "-D", "--delete"} & set(flags):
            return None if default not in names else f"deleting {default}"
        if not names or {"--list", "-l", "--show-current", "--contains", "--merged", "--no-merged"} & set(flags):
            return None
    if sub == "push" and ({"--delete", "-d"} & set(flags) or any(n.startswith(":") for n in names[1:])):
        return None
    if sub == "stash" and names[:1] and names[0] in ("list", "show", "drop"):
        return None
    if sub == "remote" and (not names or names[0] in ("show", "get-url", "prune", "update")):
        return None
    if sub == "config" and {"--get", "--get-all", "--get-regexp", "--list", "-l"} & set(flags):
        return None
    if sub == "tag" and (not names or {"-l", "--list"} & set(flags)):
        return None
    return f"git {sub}"


def check_bash(command, cwd):
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
    env, segment = {"PWD": cwd, "HOME": os.path.expanduser("~")}, []
    for tok in tokens + [";"]:
        if tok not in SEPARATORS:
            segment.append(tok)
            continue
        words, segment = segment, []
        for i, w in enumerate(words[:-1]):
            if w in REDIRECTS and base_of(expand(words[i + 1], env, cwd)):
                return f"writing into {words[i + 1]}"
        while words and re.match(r"^\w+=", words[0]):
            key, _, value = words.pop(0).partition("=")
            env[key] = None if "$" in value or "`" in value else value   # $(...) is not evaluated: unknown
        if not words:
            continue
        cmd, args = os.path.basename(words[0]), words[1:]
        if cmd == "cd" and args:
            cwd = expand(args[0], env, cwd)
        elif cmd == "git":
            where, i = cwd, 0
            while i < len(args) and args[i].startswith("-"):
                if args[i] == "-C" and i + 1 < len(args):
                    where = expand(args[i + 1], env, where)
                    i += 2
                elif args[i] in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
                    i += 2
                else:
                    i += 1
            base = base_of(where)
            rest = [subst(a, env) for a in args[i:]]
            if base and not any("$" in a for a in rest):   # an unresolved variable: cannot judge, allow
                reason = git_verdict(rest, base)
                if reason:
                    return f"{reason} in bases/{os.path.basename(base)}"
        elif cmd in WRITERS or (cmd == "sed" and any(a.startswith("-i") for a in args)):
            hit = next((a for a in args if not a.startswith("-") and base_of(expand(a, env, cwd))), None)
            if hit:
                return f"{cmd} {hit}"
    return None


def main():
    try:
        event = json.load(sys.stdin)
        tool, data = event.get("tool_name", ""), event.get("tool_input") or {}
        cwd = event.get("cwd") or os.getcwd()
        if tool == "Bash":
            reason = check_bash(data.get("command", ""), cwd)
        else:
            path = data.get("file_path") or data.get("notebook_path") or ""
            reason = f"editing {path}" if path and base_of(os.path.join(cwd, path)) else None
    except Exception:
        return   # fail open: a hook bug must never block work
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked ({reason}): the hub's bases are read-only. Only reading, syncing with origin/<default> "
                "and worktree add/list/remove/prune/repair are allowed there. Do the work in a worktree under "
                "work/<base>/ (/work-on). If this was legitimate, extend the allowlist in theflow's "
                "lib/protect-bases.py."),
        }}))


if __name__ == "__main__":
    main()
