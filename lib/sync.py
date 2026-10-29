"""theflow sync: mirror the hub's layout into the running herdr session.

Names (kept on every run, so a workspace closed and opened again gets them back; prefixes from flow.json):
- The hub itself gets a workspace labelled with the hub's name; its tabs that still have herdr's default number
  get hub:<random slug> (a name the user gave a tab is kept).
- Every base gets a workspace labelled base:<name>, rooted at the base (a linked base at its real folder).
- Every worktree of a git base that lives under work/ is a code item. It is opened as a child workspace of the
  base, labelled with the item's short name from its note (no prefix; backfill: the first words of the branch
  description, never a ticket ID), and its main tab is work:<short name>. A newly opened one also gets the base's
  build_panes, each running its command, and every run links the base's worktree_links into it.
- Every item has a note, items/<base>/<ITEM-ID>.md. A worktree without one gets a minimal note here.
- A talk item (note with `kind: talk`) has no worktree. Its Claude runs in the base workspace, rooted at the base,
  in a pane of its own titled talk:<short name> (flow.json "talk": "tab": a tab of that name).
- The Claude agent of every item is named after its short name (kept in the note as agent), and its session id is
  recorded in the note as claude_session. A note with `mcp: <name>[,<name>...]` starts that Claude with
  `--mcp-config mcp/<name>.json` for each name. Every item's Claude starts with Remote Control on, its session
  named after the agent, in a folder trusted ahead of time.
Workspaces are matched by path, never by label. Nothing is ever closed, so this is safe to re-run.

  --resume            also start Claude (`--resume <id>`) for every item that has no agent but whose note records
                      a claude_session.
  --start BASE/ITEM   start Claude for that item under its agent name: a new item gets a fresh claude_session
                      (`--session-id`), one with a transcript is resumed.
  --status            change nothing; print every item with its agent state (blocked first), and Claude agents in
                      base or worktree workspaces that no item claims (with their session id, for adopting).
                      Items idle for stale_days or more are flagged, so are items stuck in status: wrapping (a
                      teardown that failed). Then the closings in items/inbox.md since the last --mark-seen, and
                      the last line counts the active items against wip_limit.
  --mark-seen         with --status: mark those closings as seen (the SessionStart hook does this, so each new
                      hub session shows them once).
  --hub-tab           only name the tab of the current pane ($HERDR_PANE_ID) in the hub: hub:<random slug> if it
                      still has its default number, then print its label (the SessionStart hook's session title).
"""
import contextlib
import datetime
import io
import os
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path

import flowlib
from flowlib import agent_name, read_note, set_note_field, worktree_title, write_note

SLUG_WORDS = (["amber", "brisk", "calm", "deft", "eager", "fond", "keen", "lucid", "mellow", "nimble", "quiet",
               "rapid", "steady", "sunny", "tidy", "witty"],
              ["badger", "comet", "falcon", "fjord", "heron", "lynx", "maple", "otter", "pebble", "quartz", "raven",
               "spruce", "tern", "walrus", "willow", "yak"])


def is_default_tab(label):
    """herdr numbers new tabs (1, 2, ...); anything else is a name someone chose."""
    return not label or label.isdigit()


class Sync:
    def __init__(self, hub, status=False, resume=False, start=None, mark_seen=False):
        self.hub, self.root = hub, hub.root
        self.status, self.resume, self.start, self.mark_seen = status, resume, start, mark_seen
        self.p = hub.prefix
        self.rows, self.claimed, self.seen = [], set(), set()
        self.active, self.stale = 0, []
        self.refresh()

    def refresh(self):
        """Every pane, with its agent's name (only `agent list` reports names), workspace labels and tabs."""
        h = self.hub.herdr
        self.panes = h("pane", "list")["panes"]
        names = {a["pane_id"]: a.get("name") for a in h("agent", "list")["agents"]}
        for p in self.panes:
            p["name"] = names.get(p["pane_id"])
        self.labels = {w["workspace_id"]: w.get("label") for w in h("workspace", "list")["workspaces"]}
        self.tabs = h("tab", "list")["tabs"]

    def rel(self, path):
        return Path(path).relative_to(self.root) if Path(path).is_relative_to(self.root) else path

    # ---- names -------------------------------------------------------------------------------------------------

    def name_workspace(self, ws, label):
        if ws and self.labels.get(ws) != label and not self.status:
            _, err = self.hub.herdr_try("workspace", "rename", ws, label)
            print(f"!   could not rename workspace {ws} to {label}: {err}" if err
                  else f"~   {ws} renamed {self.labels.get(ws)} -> {label}")
            self.labels[ws] = label

    def name_tab(self, tab_id, label):
        tab = next((t for t in self.tabs if t["tab_id"] == tab_id), None)
        if tab and tab.get("label") != label and not self.status:
            _, err = self.hub.herdr_try("tab", "rename", tab_id, label)
            print(f"!   could not rename tab {tab_id} to {label}: {err}" if err
                  else f"~   tab {tab_id} renamed {tab.get('label')} -> {label}")
            tab["label"] = label

    def name_hub_tabs(self, ws, only_tab=None):
        """Hub tabs still carrying herdr's default number get hub:<adjective>-<noun>; chosen names stay."""
        taken = {t.get("label") for t in self.tabs}
        for t in self.tabs:
            if t["workspace_id"] == ws and is_default_tab(t.get("label")) and only_tab in (None, t["tab_id"]):
                while True:
                    label = f"{self.p['hub']}{random.choice(SLUG_WORDS[0])}-{random.choice(SLUG_WORDS[1])}"
                    if label not in taken:
                        break
                taken.add(label)
                self.name_tab(t["tab_id"], label)

    # ---- status rows -------------------------------------------------------------------------------------------

    def say(self, text):
        if not self.status:
            print(text)

    def row(self, state, label, detail):
        self.rows.append((flowlib.STATE_ORDER.get(state, 5), f"{state:<8} {label:<26} {detail}"))

    def item_row(self, state, label, detail, idle, meta=None):
        """An active item's row, flagged when nobody has touched it for stale_days or its teardown is stuck."""
        self.active += 1
        if (meta or {}).get("status") == "wrapping":
            detail = f"{detail}  [wrapping: teardown running or failed, see items/.logs/ and its note]"
        elif idle is not None and idle >= self.hub.config["stale_days"] and state not in ("working", "blocked"):
            self.stale.append(label)
            detail = f"{detail}  [idle {idle}d: wrap up or cancel?]"
        self.row(state, label, detail)

    def idle_days(self, path, meta):
        """Days since the item was last touched: its Claude transcript, its last commit or the note's created date.
        A talk item has no worktree (path None), so no commits of its own."""
        sid = meta.get("claude_session")
        stamps = [f.stat().st_mtime for f in self.hub.transcripts.glob(f"*/{sid}.jsonl")] if sid else []
        last = flowlib.out("git", "-C", str(path), "log", "-1", "--format=%ct") if path else ""
        if last.isdigit():
            stamps.append(int(last))
        try:
            stamps.append(datetime.datetime.fromisoformat(meta.get("created", "")).timestamp())
        except ValueError:
            pass
        return int((time.time() - max(stamps)) // 86400) if stamps else None

    # ---- hub and bases -----------------------------------------------------------------------------------------

    def hub_workspace(self):
        name = self.hub.config["name"]
        ws = next((p["workspace_id"] for p in self.panes if p.get("cwd") == str(self.root)), None)
        if ws:
            self.say(f"= {name:<24} {ws}")
        elif not self.status:
            ws = self.hub.herdr("workspace", "create", "--cwd", str(self.root), "--label", name,
                                "--no-focus")["workspace"]["workspace_id"]
            print(f"+ {name:<24} {ws}")
            self.refresh()
        if ws:
            self.name_hub_tabs(ws)

    def base_workspace(self, name, path, label):
        """(workspace id, worktrees) of a base: a git base's main checkout, else the workspace rooted at it."""
        trees = self.hub.herdr("worktree", "list", "--cwd", str(path))["worktrees"] if self.hub.is_git(name) else []
        main = next((t for t in trees if not t["is_linked_worktree"]), None)
        ws = main.get("open_workspace_id") if main else next(
            (p["workspace_id"] for p in self.panes if p.get("cwd") == str(path)), None)
        if ws:
            self.say(f"= {label:<24} {ws}")
            self.name_workspace(ws, label)
        elif self.status:
            self.row("-", label, "base workspace not open (run theflow sync)")
        else:
            ws = self.hub.herdr("workspace", "create", "--cwd", str(path), "--label", label,
                                "--no-focus")["workspace"]["workspace_id"]
            print(f"+ {label:<24} {ws}")
            self.refresh()
        return ws, trees

    # ---- code items --------------------------------------------------------------------------------------------

    def note_for(self, base, path, branch):
        """The item's note and its front matter; a worktree without one gets a minimal note."""
        note = self.hub.items / base / f"{path.name}.md"
        if note.exists():
            meta = read_note(note)
        else:
            label = worktree_title(branch, path.name)
            meta = {"item": path.name, "repo": base, "branch": branch or "", "label": label,
                    "agent": agent_name(label), "created": datetime.date.today().isoformat(), "status": "active"}
            sid = self.hub.last_transcript(path)   # an agent running there overrides it when adopted
            if sid:
                meta["claude_session"] = sid
            if self.status:
                return None, meta
            write_note(note, meta, "Backfilled by theflow sync: no summary yet.")
            print(f"    wrote {self.rel(note)}")
        for key, value in (("label", lambda: worktree_title(branch, path.name)), ("agent", lambda: agent_name(meta["label"]))):
            if not meta.get(key):
                meta[key] = value()
                if not self.status:
                    set_note_field(note, key, meta[key])
        return note, meta

    def worktrees(self, base, ws, trees):
        build_panes = self.hub.base_config(base).get("build_panes", [])
        for t in trees:
            if not t["is_linked_worktree"]:
                continue
            path = Path(t["path"])
            if t["is_prunable"]:
                self.say(f"! {base}: prunable worktree {path} (run `git -C bases/{base} worktree prune`)")
                continue
            if self.hub.work.resolve() not in path.parents:
                self.say(f"! {base}: worktree outside work/, skipped: {path}")
                continue
            self.seen.add((base, path.name))
            tree_ws = t.get("open_workspace_id")
            note, meta = self.note_for(base, path, t.get("branch"))
            label, name = meta["label"], meta["agent"]
            idle = self.idle_days(path, meta) if self.status else None
            if not self.status:
                self.link_files(base, path)
            if tree_ws:
                self.say(f"=   {label:<22} {tree_ws}")
                self.name_workspace(tree_ws, label)
            elif self.status:
                self.item_row("-", label, "no workspace (run theflow sync)", idle, meta)
                continue
            else:
                res = self.hub.herdr("worktree", "open", "--workspace", ws, "--path", str(path), "--label", label,
                                     "--no-focus")
                self.refresh()
                tree_ws = (res.get("workspace") or {}).get("workspace_id") or next(
                    (p["workspace_id"] for p in self.panes if p.get("cwd") == str(path)), None)
                print(f"+   {label:<22} {tree_ws or 'opened'}")
                if tree_ws and build_panes:
                    self.add_build_panes(tree_ws, path, build_panes)
            in_tree = [p for p in self.panes if p["workspace_id"] == tree_ws]
            claude = [p for p in in_tree if p.get("agent") == "claude"]
            # The item's agent: the Claude pane with its name or session id, else the one rooted at the worktree, else
            # the first Claude pane in its workspace.
            sid = meta.get("claude_session")
            agent = next((p for p in claude if p.get("name") == name
                          or (sid and (p.get("agent_session") or {}).get("value") == sid)), None) \
                or next((p for p in claude if p.get("cwd") == str(path)), claude[0] if claude else None)
            if agent:
                self.claimed.add(agent["pane_id"])
            # Its main tab (the agent's, else the workspace's first): work:<short name>, unless the user named it.
            main_tab = (agent or {}).get("tab_id") or next(
                (t["tab_id"] for t in self.tabs if t["workspace_id"] == tree_ws), None)
            tab_label = next((t.get("label") for t in self.tabs if t["tab_id"] == main_tab), None)
            if main_tab and (is_default_tab(tab_label) or tab_label.startswith(self.p["work"])):
                self.name_tab(main_tab, f"{self.p['work']}{label}")
            if self.status:
                if agent:
                    self.item_row(agent["agent_status"], label, agent.get("name") or f"{name} (unnamed)", idle, meta)
                else:
                    hint = f"resume: theflow sync --start {base}/{path.name}" if meta.get("claude_session") else ""
                    self.item_row("-", label, f"no agent  {hint}".rstrip(), idle, meta)
                continue
            wanted = self.start == f"{base}/{path.name}"
            # A pane to start Claude in: the first one at the worktree root that runs no agent
            # (the workspace's own pane comes before any build panes split off it).
            free = next((p for p in in_tree if not p.get("agent") and p.get("cwd") == str(path)), None)
            if agent:
                self.adopt(agent, name, note, meta)
                if wanted:
                    print(f"=   {name} is already running")
            elif free and (wanted or (self.resume and meta.get("claude_session"))):
                self.start_claude(free["pane_id"], name, note, meta)
            elif wanted:
                print(f"!   no free pane at {path} to start {name} in")
            elif meta.get("claude_session"):
                print(f"    resume with: theflow sync --start {base}/{path.name}")

    def link_files(self, base, path):
        """Symlink the base's worktree_links (untracked files the work needs: .env, local data) into the worktree,
        each also excluded from git there (a symlink doesn't match a `dir/` ignore rule)."""
        links = self.hub.base_config(base).get("worktree_links", [])
        if not links:
            return
        src_root = self.hub.base_path(base)
        exclude = Path(flowlib.out("git", "-C", str(path), "rev-parse", "--path-format=absolute",
                                   "--git-common-dir")) / "info" / "exclude"
        for rel in links:
            src, dst = src_root / rel, path / rel
            if not src.exists() or dst.exists() or dst.is_symlink():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(os.path.relpath(src, dst.parent))
            excluded = exclude.read_text().splitlines() if exclude.exists() else []
            if f"/{rel}" not in excluded:
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with exclude.open("a") as f:
                    f.write(f"/{rel}\n")
            print(f"    linked {rel} into {self.rel(path)}")

    def add_build_panes(self, ws, path, commands):
        """Split the worktree's first pane: the first command to the right, the rest below it."""
        target, direction = next(p["pane_id"] for p in self.panes if p["workspace_id"] == ws), "right"
        for cmd in commands:
            res, err = self.hub.herdr_try("pane", "split", target, "--direction", direction, "--cwd", str(path),
                                          "--no-focus")
            if err:
                print(f"!   could not split a pane for `{cmd}`: {err}")
                return
            target, direction = res["pane"]["pane_id"], "down"
            self.hub.herdr_try("pane", "run", target, cmd)
            print(f"    pane {target}: {cmd}")
        self.refresh()

    # ---- talk items --------------------------------------------------------------------------------------------

    def talks(self, base, path, ws):
        """Talk items (kind: talk): no worktree; each one's Claude runs in the base workspace."""
        for note in sorted((self.hub.items / base).glob("*.md")):
            meta = read_note(note)
            if meta.get("kind") != "talk":
                continue
            item = note.stem
            self.seen.add((base, item))
            label = meta.get("label") or item
            name, sid = meta.get("agent") or agent_name(label), meta.get("claude_session")
            title = f"{self.p['talk']}{label}"
            if not meta.get("agent") and not self.status:
                set_note_field(note, "agent", name)
            # Its agent: the Claude pane in the base workspace with its name, or anywhere with its session id (an
            # adopted session stays where it was started).
            agent = next((p for p in self.panes if p["workspace_id"] == ws and p.get("agent") == "claude"
                          and p.get("name") == name), None) or next(
                (p for p in self.panes if sid and p.get("agent") == "claude" and p["pane_id"] not in self.claimed
                 and (p.get("agent_session") or {}).get("value") == sid), None)
            if agent:
                self.claimed.add(agent["pane_id"])
            if self.status:
                idle = self.idle_days(None, meta)
                if agent:
                    self.item_row(agent["agent_status"], label, f"{agent.get('name') or name + ' (unnamed)'}  (talk)",
                                  idle, meta)
                else:
                    hint = f"resume: theflow sync --start {base}/{item}" if sid else ""
                    self.item_row("-", label, f"no agent  (talk)  {hint}".rstrip(), idle, meta)
                continue
            self.say(f"=   {label:<22} talk, pane {agent['pane_id'] if agent else '-'}")
            wanted = self.start == f"{base}/{item}"
            if agent:
                self.adopt(agent, name, note, meta)
                own_tab = [t for t in self.tabs if t["workspace_id"] == agent["workspace_id"]][:1] != \
                    [t for t in self.tabs if t["tab_id"] == agent["tab_id"]]   # not its workspace's first tab
                if self.hub.config["talk"] == "tab" and own_tab:
                    tab_label = next((t.get("label") for t in self.tabs if t["tab_id"] == agent["tab_id"]), None)
                    if is_default_tab(tab_label) or tab_label.startswith(self.p["talk"]) or tab_label == label:
                        self.name_tab(agent["tab_id"], title)
                elif self.hub.config["talk"] != "tab":
                    self.hub.herdr_try("pane", "rename", agent["pane_id"], title)   # herdr doesn't report it
                if wanted:
                    print(f"=   {name} is already running")
            elif wanted or (self.resume and sid):
                pane = self.talk_pane(ws, path, title)
                if pane:
                    self.start_claude(pane, name, note, meta)
            elif sid:
                print(f"    resume with: theflow sync --start {base}/{item}")

    def talk_pane(self, ws, path, title):
        """A place for a talk item's Claude in the base workspace, rooted at the base: a tab of its own (talk: tab),
        reusing an empty one of that name, or a new pane (the first to the right, the rest below it)."""
        if self.hub.config["talk"] == "tab":
            tab = next((t["tab_id"] for t in self.tabs if t["workspace_id"] == ws and t.get("label") == title), None)
            free = next((p for p in self.panes if p["tab_id"] == tab and not p.get("agent")), None) if tab else None
            if free:
                return free["pane_id"]
            res, err = self.hub.herdr_try("tab", "create", "--workspace", ws, "--cwd", str(path), "--label", title,
                                          "--no-focus")
            if err:
                print(f"!   could not create a tab in the base workspace {ws}: {err}")
                return None
            self.refresh()
            tab = res["tab"]["tab_id"]
            print(f"+   tab {tab} ({title}) in the base workspace")
            return next(p["pane_id"] for p in self.panes if p["tab_id"] == tab)
        in_ws = [p for p in self.panes if p["workspace_id"] == ws]
        res, err = self.hub.herdr_try("pane", "split", in_ws[-1]["pane_id"], "--direction",
                                      "right" if len(in_ws) == 1 else "down", "--cwd", str(path), "--no-focus")
        if err:
            print(f"!   could not split a pane in the base workspace {ws}: {err}")
            return None
        pane = res["pane"]["pane_id"]
        self.hub.herdr_try("pane", "rename", pane, title)
        self.refresh()
        print(f"+   pane {pane} ({title}) in the base workspace")
        return pane

    # ---- agents ------------------------------------------------------------------------------------------------

    def adopt(self, pane, name, note, meta):
        """Name the item's Claude agent and record its session id in the note."""
        if pane.get("name") != name:
            _, err = self.hub.herdr_try("agent", "rename", pane["pane_id"], name)
            print(f"!   could not name agent {name}: {err}" if err else f"    named agent {name}")
        sid = (pane.get("agent_session") or {}).get("value")
        if sid and sid != meta.get("claude_session"):
            set_note_field(note, "claude_session", sid)
            meta["claude_session"] = sid
            print(f"    recorded claude_session {sid}")

    def start_claude(self, pane_id, name, note, meta):
        sid = meta.get("claude_session")
        if not sid:
            sid = str(uuid.uuid4())
            set_note_field(note, "claude_session", sid)
            meta["claude_session"] = sid
        flag = "--resume" if self.hub.has_transcript(sid) else "--session-id"
        home = self.hub.config["claude_home"]
        if home and Path(os.path.expanduser(home)).resolve() != Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser().resolve():
            print(f"!   this herdr server was not started with the hub's Claude home ({home}): {name} would run "
                  "with another one. Stop the server and reopen it with `theflow open`")
            return
        cwd = next((p.get("cwd") for p in self.panes if p["pane_id"] == pane_id), None)
        trusted = self.hub.trust(cwd) if cwd else None
        if trusted:
            print(f"    trusted {trusted} for Claude")
        extra = ["--remote-control", name]
        for server in filter(None, (s.strip() for s in meta.get("mcp", "").split(","))):
            config = self.hub.mcp / f"{server}.json"
            if config.exists():
                extra += ["--mcp-config", str(config)]
            else:
                print(f"!   {name}: no MCP config {self.rel(config)}, starting without it")
        _, err = self.hub.herdr_try("agent", "start", name, "--kind", "claude", "--pane", pane_id,
                                    "--timeout", "60000", "--", flag, sid, *extra)
        if err and err.startswith("agent_not_ready"):
            print(f"!   {name} is waiting on a startup dialog: answer it in its pane {pane_id}")
        elif err:
            print(f"!   could not start {name}: {err}")
        else:
            print(f"    started {name} (claude {flag} {sid}, remote control on)")

    def orphans(self, base_label, base_ws):
        """Claude agents in a base's workspaces that no item claims, with what adopting them needs."""
        for p in self.panes:
            if p.get("agent") == "claude" and p["workspace_id"] in base_ws and p["pane_id"] not in self.claimed:
                where = self.labels.get(p["workspace_id"]) or base_label
                title = p.get("terminal_title_stripped") or "untitled"
                sid = (p.get("agent_session") or {}).get("value") or "?"
                self.rows.append((6, f"{p['agent_status']:<8} {where:<26} no item: pane {p['pane_id']} \"{title}\""
                                     f" session {sid}"))

    def stray_notes(self):
        """Active notes whose worktree (or base workspace) is gone."""
        for note in sorted(self.hub.items.glob("*/*.md")):
            base, item = note.parent.name, note.stem
            meta = read_note(note)
            if (base, item) not in self.seen and meta.get("item"):   # claude-md-backlog.md is not an item
                label = meta.get("label") or f"{base}/{item}"
                where = f"talk, base {base} has no workspace (run theflow sync)" if meta.get("kind") == "talk" \
                    else f"no worktree work/{base}/{item}"
                self.row("-", label, f"{where} (note {self.rel(note)})")

    def closings(self):
        """Inbox entries newer than the seen marker: `closed  <label>  <verdict> (<by>) <when>, <teardown result>`."""
        if not self.hub.inbox.exists():
            return
        blocks = self.hub.inbox.read_text().split("\n## ")[1:]
        seen = self.hub.inbox_seen.read_text().strip() if self.hub.inbox_seen.exists() else "0"
        for block in blocks[int(seen) if seen.isdigit() else 0:]:
            head, _, body = block.partition("\n")
            stamp, label, verdict = (head.split(" · ") + ["", ""])[:3]
            result = "teardown clean" if "Nothing left." in body else "something LEFT, see items/inbox.md"
            print(f"closed   {label:<26} {verdict} {stamp[5:]}, {result}")
        if self.mark_seen:
            self.hub.inbox_seen.write_text(f"{len(blocks)}\n")

    def run(self):
        self.hub_workspace()
        for base in self.hub.base_names():
            path = self.hub.base_path(base)
            label = f"{self.p['base']}{base}"
            ws, trees = self.base_workspace(base, path, label)
            self.seen |= {(base, Path(t["path"]).name) for t in trees if t["is_linked_worktree"]}
            if not ws:
                continue
            self.worktrees(base, ws, trees)
            self.talks(base, path, ws)
            if self.status:
                self.orphans(label, {ws} | {t["open_workspace_id"] for t in trees if t.get("open_workspace_id")})
        if self.status:
            self.stray_notes()
            for _, row in sorted(self.rows, key=lambda r: r[0]):
                print(row)
            self.closings()
            limit = self.hub.config["wip_limit"]
            if not self.rows:
                print("no active work items")
            elif self.active > limit:
                print(f"{self.active} active items, over the limit of {limit}: wrap up or cancel some before "
                      f"starting more" + (f" (idle {self.hub.config['stale_days']}d+: {', '.join(self.stale)})"
                                          if self.stale else ""))
            else:
                print(f"{self.active} active items (limit {limit})")


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return
    start = args[args.index("--start") + 1] if "--start" in args and args.index("--start") + 1 < len(args) else None
    if "--start" in args and not start:
        sys.exit("usage: theflow sync --start <base>/<ITEM-ID>")
    hub = flowlib.Hub()
    if not hub.herdr_running():
        sys.exit("herdr is not running; open it with `theflow open`")
    if "--hub-tab" in args:
        sync = Sync(hub)
        pane = next((p for p in sync.panes if p["pane_id"] == os.environ.get("HERDR_PANE_ID")), None)
        if not pane:
            return
        with contextlib.redirect_stdout(io.StringIO()):   # only the label goes out: it becomes the session title
            sync.name_hub_tabs(pane["workspace_id"], only_tab=pane["tab_id"])
        print(next((t.get("label") for t in sync.tabs if t["tab_id"] == pane["tab_id"]), ""))
        return
    Sync(hub, status="--status" in args, resume="--resume" in args, start=start,
         mark_seen="--mark-seen" in args).run()


if __name__ == "__main__":
    main()
