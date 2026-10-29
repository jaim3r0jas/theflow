"""Shared by every theflow command: the hub (its folder and flow.json), item notes, herdr and Claude's config.

A hub is a folder with a flow.json:

    <hub>/
    ├── flow.json           settings (see DEFAULTS) and per-base options
    ├── CLAUDE.md           imports theflow's HUB.md, then the hub's own rules
    ├── bases/<name>        a git clone owned by the hub (read-only, reset to origin on sync), or a symlink to a
    │                       folder of yours anywhere (linked: never reset; a folder without git hosts talk items only)
    ├── work/<base>/<ID>    one git worktree per code item
    ├── items/<base>/<ID>.md   one note per item; finished ones in items/<base>/done/
    └── mcp/<name>.json     optional MCP configs an item can ask for (note field mcp: <name>)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FLOW = Path(__file__).resolve().parent.parent       # the theflow checkout
CONFIG_NAME = "flow.json"

DEFAULTS = {
    "name": "hub",                 # label of the hub's own herdr workspace
    "herdr_session": "",           # herdr session to use ("" = herdr's default one)
    "claude_home": "",             # Claude config dir for this hub's sessions ("" = $CLAUDE_CONFIG_DIR or ~/.claude)
    "remote_control_prefix": "",   # name prefix of unnamed Remote Control sessions started under `theflow open`
    "wip_limit": 6,                # active items one person can review properly; work-on warns above it
    "stale_days": 7,               # an item untouched this long is a candidate for wrap-up (or cancel)
    "talk": "pane",                # where a talk item's Claude runs in its base workspace: "pane" or "tab"
    "branch": {"ticket": "feat/{ticket}/{desc}", "adhoc": "feat/ad-hoc/{desc}"},
    "prefixes": {"base": "base:", "work": "work:", "talk": "talk:", "hub": "hub:"},
    "noise": [".husky/_/"],        # untracked paths that never count as pending work
    "bases": {},                   # per base: build_panes, worktree_links, merge ("pr" | "local"), writable
}
STATE_ORDER = {"blocked": 0, "done": 1, "working": 2, "unknown": 3, "idle": 4}


def find_root(start=None):
    """The hub folder: the nearest flow.json above the working directory (the logical $PWD first, so a symlinked
    base still finds its hub), else $FLOW_HUB. None outside any hub."""
    starts = [Path(start)] if start else [Path(os.environ.get("PWD", ".")), Path.cwd()]
    for s in starts:
        for d in [s, *s.parents]:
            if (d / CONFIG_NAME).is_file():
                return d.resolve()
    env = os.environ.get("FLOW_HUB")
    if env and (Path(env) / CONFIG_NAME).is_file():
        return Path(env).resolve()
    return None


def merged(defaults, own):
    out = dict(defaults)
    for k, v in own.items():
        out[k] = merged(defaults[k], v) if isinstance(v, dict) and isinstance(defaults.get(k), dict) else v
    return out


class Hub:
    def __init__(self, root=None):
        root = root or find_root()
        if not root:
            sys.exit("theflow: not inside a hub (no flow.json above this folder, and $FLOW_HUB is not set). "
                     "Create one with `theflow init <dir>`.")
        self.root = Path(root).resolve()
        self.config = merged(DEFAULTS, json.loads((self.root / CONFIG_NAME).read_text()))
        self.bases, self.work, self.items = self.root / "bases", self.root / "work", self.root / "items"
        self.mcp = self.root / "mcp"
        self.inbox = self.items / "inbox.md"             # every closing, appended by `theflow teardown`
        self.inbox_seen = self.items / ".inbox-seen"     # how many inbox entries a new hub session has been shown
        self.logs = self.items / ".logs"
        self.prefix = self.config["prefixes"]
        self.herdr_cmd = ["herdr"] + (["--session", self.config["herdr_session"]] if self.config["herdr_session"] else [])

    # ---- config ------------------------------------------------------------------------------------------------

    @property
    def claude_home(self):
        home = self.config["claude_home"] or os.environ.get("CLAUDE_CONFIG_DIR") or ""
        return Path(os.path.expanduser(home)) if home else Path.home() / ".claude"

    @property
    def claude_json(self):
        """Claude's global config (trust, projects): inside a custom config dir, else ~/.claude.json."""
        custom = self.config["claude_home"] or os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(os.path.expanduser(custom)) / ".claude.json" if custom else Path.home() / ".claude.json"

    @property
    def transcripts(self):
        return self.claude_home / "projects"

    def base_config(self, name):
        return self.config["bases"].get(name, {})

    def base_path(self, name):
        """The folder a base stands for (a linked base's real folder)."""
        return (self.bases / name).resolve()

    def is_linked(self, name):
        return (self.bases / name).is_symlink()

    def is_git(self, name):
        return (self.base_path(name) / ".git").exists()

    def writable(self, name):
        """Whether sessions may change files in the base itself: by default only a linked folder of yours (an owned
        one is guarded by lib/protect-bases.py, which reads the same setting)."""
        return self.base_config(name).get("writable", self.is_linked(name))

    def merge_mode(self, name):
        """How an item's branch lands: "pr" (merged on the forge) or "local" (fast-forwarded into the base)."""
        return self.base_config(name).get("merge") or ("pr" if self.github_repo(name) else "local")

    def github_repo(self, name):
        """owner/repo of the base's origin on GitHub, else None."""
        url = out("git", "-C", str(self.base_path(name)), "remote", "get-url", "origin")
        hit = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
        return hit.group(1) if hit else None

    def default_branch(self, name):
        d = out("git", "-C", str(self.base_path(name)), "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        return d.removeprefix("origin/") or "main"

    def base_names(self):
        return sorted(p.name for p in self.bases.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if self.bases.exists() else []

    # ---- herdr -------------------------------------------------------------------------------------------------

    def herdr_running(self):
        """`herdr status server` exits 0 even when the server is down: read its status line instead."""
        res = subprocess.run([*self.herdr_cmd, "status", "server"], capture_output=True, text=True)
        return res.stdout.startswith("status: running")

    def herdr(self, *args):
        res, err = self.herdr_try(*args)
        if err:
            sys.exit(f"herdr {' '.join(args)} failed: {err}")
        return res

    def herdr_try(self, *args):
        """(result, None), or (None, error message)."""
        res = subprocess.run([*self.herdr_cmd, *args], capture_output=True, text=True)
        try:
            data = json.loads(res.stdout or res.stderr)
        except json.JSONDecodeError:
            return None, res.stderr.strip() or res.stdout.strip()
        if "error" in data:
            return None, f"{data['error'].get('code', '')}: {data['error']['message']}"
        return data["result"], None

    # ---- Claude ------------------------------------------------------------------------------------------------

    def has_transcript(self, sid):
        return any(self.transcripts.glob(f"*/{sid}.jsonl"))

    def session_title(self, sid):
        """The name a Claude session carries (`--name`, `/rename` or a hook's sessionTitle): the last one in its newest
        transcript."""
        for f in sorted(self.transcripts.glob(f"*/{sid}.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True):
            data = f.read_bytes()
            at = data.rfind(b'"type":"custom-title"')
            if at >= 0:
                start, end = data.rfind(b"\n", 0, at) + 1, data.find(b"\n", at)
                try:
                    return json.loads(data[start:end if end >= 0 else None]).get("customTitle")
                except ValueError:
                    return None
        return None

    def last_transcript(self, path):
        """The id of the newest Claude conversation started in path, if any."""
        found = sorted((self.transcripts / re.sub(r"[^A-Za-z0-9]", "-", str(path))).glob("*.jsonl"),
                       key=lambda f: f.stat().st_mtime)
        return found[-1].stem if found else None

    def trust(self, path):
        """Accept Claude's folder-trust dialog ahead of time, so a session started there by theflow runs (and
        Remote Control connects) without anyone at the keyboard. Claude keys a git worktree's trust on its main
        checkout, so that is the folder trusted. Returns the folder newly trusted, or None."""
        path = Path(path).resolve()
        common = out("git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common:
            path = Path(common).parent
        config = self.claude_json
        try:
            data = json.loads(config.read_text())
        except (OSError, json.JSONDecodeError):
            return None   # no Claude config yet (never run with this home): Claude asks once itself
        if any(data.get("projects", {}).get(str(p), {}).get("hasTrustDialogAccepted") for p in [path, *path.parents]):
            return None
        data.setdefault("projects", {}).setdefault(str(path), {})["hasTrustDialogAccepted"] = True
        tmp = config.with_name(config.name + ".theflow")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(config)
        return path


# ---- notes ---------------------------------------------------------------------------------------------------


def read_note(path):
    """The key: value lines of a note's front matter."""
    meta, lines = {}, Path(path).read_text().splitlines()
    if lines[:1] != ["---"]:
        return meta
    for line in lines[1:]:
        if line == "---":
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.split(" #")[0].strip()
    return meta


def set_note_field(path, key, value):
    """Set one front-matter field of a note, adding it before the closing --- if missing."""
    lines = Path(path).read_text().splitlines(keepends=True)
    end = next(i for i, line in enumerate(lines[1:], 1) if line.rstrip("\n") == "---")
    for i in range(1, end):
        if lines[i].partition(":")[0].strip() == key:
            lines[i] = f"{key}: {value}\n"
            break
    else:
        lines.insert(end, f"{key}: {value}\n")
    Path(path).write_text("".join(lines))


def write_note(path, meta, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    front = "".join(f"{k}: {v}\n" for k, v in meta.items())
    path.write_text(f"---\n{front}---\n{body}\n")


def worktree_title(branch, fallback):
    """feat/ABC-1/retry-failed-sync -> retry-failed-sync (at most 3 words, ~20 chars, never a ticket ID)."""
    desc = (branch or "").rsplit("/", 1)[-1] or fallback
    desc = re.sub(r"^[A-Za-z]+-\d+[-_]?", "", desc) or "work"
    words = desc.lower().replace("_", "-").split("-")[:3]
    while len(words) > 1 and len("-".join(words)) > 20:
        words.pop()
    return "-".join(words)


def agent_name(label):
    """A label made safe for herdr agent names ([a-z][a-z0-9_-]{0,31})."""
    name = re.sub(r"[^a-z0-9_-]+", "-", label.lower()).strip("-")
    if not name[:1].isalpha():
        name = f"x-{name}"
    return name[:32].rstrip("-")


# ---- processes -----------------------------------------------------------------------------------------------


def sh(*cmd, cwd=None):
    """(returncode, stdout, stderr) of a command, never raising."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError as e:
        return 127, "", str(e)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def out(*cmd, cwd=None):
    return sh(*cmd, cwd=cwd)[1]
