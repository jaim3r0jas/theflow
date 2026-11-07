"""theflow admin commands: init, install, doctor, root, env, repo, trust."""
import json
import os
import shutil
import sys
from pathlib import Path

import flowlib
from flowlib import FLOW, Hub

SKILLS = ["work-on", "wrap-up", "review"]
PROTECT = f'"{FLOW}/bin/theflow" hook protect-bases'
SESSION_START = f'"{FLOW}/bin/theflow" hook session-start'
STATUSLINE = {"type": "command", "command": f'bash "{FLOW}/lib/statusline.sh"', "refreshInterval": 1}


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".theflow")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def has_hook(settings, event, command):
    return any(h.get("command") == command for m in settings.get("hooks", {}).get(event, []) for h in m.get("hooks", []))


def add_hook(settings, event, matcher, command):
    if has_hook(settings, event, command):
        return False
    settings.setdefault("hooks", {}).setdefault(event, []).append(
        {"matcher": matcher, "hooks": [{"type": "command", "command": command}]})
    return True


def remove_hooks(settings, event, is_ours):
    """Drop the event's hooks whose command is_ours, and any group or event they leave empty. True if one went."""
    groups, kept, removed = settings.get("hooks", {}).get(event, []), [], False
    for m in groups:
        hooks = [h for h in m.get("hooks", []) if not is_ours(h.get("command", ""))]
        if len(hooks) == len(m.get("hooks", [])):
            kept.append(m)   # none of ours: kept as it is, even a group without hooks
            continue
        removed = True
        if hooks:
            kept.append({**m, "hooks": hooks})
    if not removed:
        return False
    if kept:
        settings["hooks"][event] = kept
    else:
        del settings["hooks"][event]
        if not settings["hooks"]:
            del settings["hooks"]
    return True


def is_theflows_session_start(command):
    """The SessionStart hook of some theflow checkout (this one, or one that moved)."""
    return command.startswith('"') and command.endswith('/bin/theflow" hook session-start')


def is_theflows_status_line(status_line):
    """A statusLine running some theflow checkout's lib/statusline.sh (this one, or one that moved)."""
    command = status_line.get("command", "") if isinstance(status_line, dict) else ""
    return command.startswith('bash "') and command.endswith('/lib/statusline.sh"')


def init(args):
    """theflow init <dir> [--name <name>] [--claude-home <dir>] [--herdr-session <name>] [--no-install]: create a hub
    (or complete one), then run `theflow install` in it."""
    if not args or args[0].startswith("-"):
        sys.exit("usage: theflow init <dir> [--name <hub name>] [--claude-home <dir>] [--herdr-session <name>] "
                 "[--no-install]")
    root = Path(args[0]).expanduser().absolute()
    opt = lambda flag, default="": args[args.index(flag) + 1] if flag in args[:-1] else default
    name = opt("--name", root.name)
    for d in ("bases", "work", "items"):
        (root / d).mkdir(parents=True, exist_ok=True)
    config = root / "flow.json"
    if not config.exists():
        own = {"name": name, "claude_home": opt("--claude-home"), "herdr_session": opt("--herdr-session")}
        save_json(config, {**{k: v for k, v in own.items() if v}, "bases": {}})
        print(f"+ {config}")
    name = json.loads(config.read_text()).get("name", name)
    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        text = (FLOW / "templates" / "CLAUDE.md").read_text().replace("{{name}}", name).replace("{{flow}}", str(FLOW))
        claude_md.write_text(text)
        print(f"+ {claude_md} (fill in its own sections)")
    else:
        print(f"= {claude_md} kept: make sure it imports @{FLOW}/HUB.md")
    hub = Hub(root)
    trusted = hub.trust(root)
    if trusted:
        print(f"+ trusted {trusted} for Claude")
    if "--no-install" in args:
        print(f"Skipped the install: run `theflow install` in {root} to link the skills, the base guard, the hub "
              f"welcome hook and the status line into {hub.claude_home}.")
    else:
        install([], hub)
    print(f"\nNext: add bases (git clone <url> {root}/bases/<name>, or ln -s <your folder> {root}/bases/<name>),"
          f"\nthen `theflow open` from {root} and start Claude in its hub workspace.")


def install(args, hub=None):
    """theflow install: link the skills, the base guard, the SessionStart hook and (unless it has one) the status line
    into this hub's Claude home, theflow onto PATH. The hook runs for every session there and greets only hub
    sessions (lib/session-start.py); a copy left in the hub's own .claude/settings.json (older theflow) is removed,
    so the hub root never greets twice."""
    hub = hub or Hub()
    home = hub.claude_home
    skills = home / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    for name in SKILLS:
        link, target = skills / name, FLOW / "skills" / name
        if link.is_symlink():
            if link.resolve() == target.resolve():
                continue
            print(f"~ {link} pointed to {os.readlink(link)}; now {target}")
            link.unlink()
        elif link.exists():
            print(f"! {link} is a folder of its own: left alone (move it away to use theflow's {name})")
            continue
        else:
            print(f"+ skill {name} -> {link}")
        link.symlink_to(target)
    settings_path = home / "settings.json"
    settings = load_json(settings_path)
    changed = add_hook(settings, "PreToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit", PROTECT)
    if changed:
        print(f"+ PreToolUse base guard in {settings_path}")
    if add_hook(settings, "SessionStart", "startup|clear", SESSION_START):
        changed = True
        print(f"+ SessionStart hook in {settings_path}")
    hub_settings_path = hub.root / ".claude" / "settings.json"
    hub_settings = load_json(hub_settings_path)
    if hub_settings_path != settings_path and remove_hooks(hub_settings, "SessionStart", is_theflows_session_start):
        save_json(hub_settings_path, hub_settings)
        print(f"- SessionStart hook in {hub_settings_path} (now in {settings_path})")
    own = settings.get("statusLine")
    if not own:
        settings["statusLine"] = STATUSLINE
        changed = True
        print(f"+ status line in {settings_path}")
    elif is_theflows_status_line(own):
        # theflow's line from this or another checkout: point it here, keep what was tuned (refreshInterval, padding)
        updated = {**STATUSLINE, **own, "command": STATUSLINE["command"]}
        if updated != own:
            settings["statusLine"] = updated
            changed = True
            print(f"~ status line in {settings_path}: now {STATUSLINE['command']}")
    else:
        print(f"= {settings_path} has a statusLine of its own: left alone (theflow's: {STATUSLINE['command']})")
    if changed:
        save_json(settings_path, settings)
    bin_dir = Path.home() / ".local" / "bin"
    exe = bin_dir / "theflow"
    if not shutil.which("theflow") and not exe.exists():
        bin_dir.mkdir(parents=True, exist_ok=True)
        exe.symlink_to(FLOW / "bin" / "theflow")
        print(f"+ {exe}" + ("" if str(bin_dir) in os.environ.get("PATH", "").split(":") else
                            f" (add {bin_dir} to PATH)"))


def doctor(args):
    """theflow doctor: what this hub needs, and what is missing."""
    ok = True

    def check(good, text, fix=""):
        nonlocal ok
        ok &= bool(good)
        print(f"{'ok ' if good else 'NO '} {text}" + ("" if good or not fix else f"  -> {fix}"))

    for tool, why in (("git", ""), ("herdr", "https://herdr.dev"), ("claude", "Claude Code"), ("gh", "GitHub CLI"),
                      ("jq", ""), ("lsof", "")):
        check(shutil.which(tool), f"{tool} on PATH", why)
    if not shutil.which("docker"):
        print("--  docker not on PATH (optional: only for items that start compose stacks)")
    check(sys.version_info >= (3, 9), f"python {sys.version.split()[0]} (3.9+)")
    check(shutil.which("theflow"), "theflow on PATH", "theflow install")
    root = flowlib.find_root()
    check(root, f"inside a hub ({root})" if root else "inside a hub", "cd into one, or theflow init <dir>")
    if not root:
        sys.exit(1)
    hub = Hub(root)
    home = hub.claude_home
    for name in SKILLS:
        link = home / "skills" / name
        check(link.is_symlink() and link.resolve() == (FLOW / "skills" / name).resolve(),
              f"skill {name} linked in {home / 'skills'}", "theflow install")
    settings = load_json(home / "settings.json")
    check(has_hook(settings, "PreToolUse", PROTECT), "base guard hook", "theflow install")
    status_line = settings.get("statusLine")
    if is_theflows_status_line(status_line):
        check(status_line["command"] == STATUSLINE["command"], "theflow's status line", "theflow install")
    elif status_line:
        print(f"--  a status line of your own (for theflow's: remove statusLine from {home / 'settings.json'}, "
              "then theflow install)")
    else:
        print("--  no status line (theflow install sets theflow's)")
    check(has_hook(settings, "SessionStart", SESSION_START), "SessionStart hook (the hub welcome)", "theflow install")
    hub_settings = root / ".claude" / "settings.json"
    check(hub_settings == home / "settings.json"
          or not remove_hooks(load_json(hub_settings), "SessionStart", is_theflows_session_start),
          f"no SessionStart hook left in {hub_settings} (the hub root would greet twice)", "theflow install")
    claude_md = root / "CLAUDE.md"
    imports = {f"@{FLOW}/HUB.md"} | ({f"@~/{FLOW.relative_to(Path.home())}/HUB.md"}
                                      if FLOW.is_relative_to(Path.home()) else set())
    check(claude_md.exists() and any(i in claude_md.read_text() for i in imports), "CLAUDE.md imports theflow's HUB.md",
          f"add the line @{FLOW}/HUB.md")
    check(hub.herdr_running(), f"herdr server running{' (session ' + hub.config['herdr_session'] + ')' if hub.config['herdr_session'] else ''}",
          "theflow open")
    for name in hub.base_names():
        kind = ("linked" if hub.is_linked(name) else "owned") + (" git" if hub.is_git(name) else " folder")
        print(f"    base {name}: {kind}, merge {hub.merge_mode(name) if hub.is_git(name) else '-'}, "
              f"{'writable' if hub.writable(name) else 'read-only'}")
    if "talk" in hub.config:
        print('--  flow.json "talk" is no longer read (talk items always get a tab of their own): remove it')
    sys.exit(0 if ok else 1)


def env(args):
    """Shell exports for `theflow open`: what every pane of the hub's herdr server inherits."""
    hub = Hub()
    exports = {"FLOW_HUB": str(hub.root)}
    if hub.config["claude_home"]:
        exports["CLAUDE_CONFIG_DIR"] = str(hub.claude_home)
    if hub.config["remote_control_prefix"]:
        exports["CLAUDE_REMOTE_CONTROL_SESSION_NAME_PREFIX"] = hub.config["remote_control_prefix"]
    for k, v in exports.items():
        print(f"export {k}={json.dumps(v)}")
    print(f"HERDR=({' '.join(json.dumps(a) for a in hub.herdr_cmd)})")


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "init":
        init(args)
    elif cmd == "install":
        install(args)
    elif cmd == "doctor":
        doctor(args)
    elif cmd == "root":
        print(Hub().root)
    elif cmd == "env":
        env(args)
    elif cmd == "repo":   # theflow repo <base>: owner/repo of its GitHub origin
        hub = Hub()
        repo = hub.github_repo(args[0]) if args else None
        print(repo) if repo else sys.exit(f"{args[0] if args else '?'}: no GitHub origin")
    elif cmd == "pull":   # theflow pull <base>: its default branch up to date with origin, before a new worktree
        hub = Hub()
        if not args or not (hub.bases / args[0]).exists():
            sys.exit(f"usage: theflow pull <base> (one of: {', '.join(hub.base_names())})")
        ok, msg, start = hub.pull(args[0])
        if not ok:
            sys.exit(msg)
        print(msg + (f"\nstart {start}" if start else ""))
    elif cmd == "trust":  # theflow trust <dir>: accept Claude's folder trust for it ahead of time
        trusted = Hub().trust(Path(args[0] if args else ".").absolute())
        print(f"trusted {trusted}" if trusted else "already trusted (or no Claude config yet)")


if __name__ == "__main__":
    main()
