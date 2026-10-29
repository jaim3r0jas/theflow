"""theflow teardown: inventory and tear down one item, the mechanical half of wrap-up.

  theflow teardown --check  <base>/<ITEM-ID>          read-only: what the item holds and what is still pending
  theflow teardown --run    <base>/<ITEM-ID> [opts]   tear it down now, from a session outside the worktree
  theflow teardown --detach <base>/<ITEM-ID> [opts]   the same in the background, for a session wrapping itself up:
                                                      it waits until that session's agent is idle (its turn
                                                      ended), then runs from the hub root, so closing the
                                                      workspace does not kill it

Options for --run and --detach (each one only on the user's yes):
  --merge            first fast-forward the base's checkout to the item's branch (a base with merge: local)
  --discard          pending work may go: force the worktree remove, delete unmerged branches
  --delete-remote    also delete origin/<branch> when it was not merged through a PR
  --close-pr         close the item's open PR and delete its branch (cancel)
  --drop-stash SHA   drop that stash (repeatable)
  --cancelled        the note ends as status: cancelled (default: done)

It refuses when the check finds pending work and --discard is missing, and when the item's agent is working or
blocked (except the session --detach waits for). The note's Outcome must already be written. After verifying, it
moves the note to items/<base>/done/, appends the closing to items/inbox.md, nudges the session that opened the
item (note field opened_by) if it is alive and idle, and shows a herdr notification. A failed teardown leaves the
note in place with status: wrapping, and reports what is left. Log of a --detach run:
items/.logs/<base>-<ITEM-ID>-teardown.log.

A talk item (kind: talk) has no worktree: nothing can be pending, and the teardown only closes its agent's pane (or
tab) in the base workspace, then records the closing the same way.
"""
import datetime
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

import flowlib
from flowlib import out, read_note, set_note_field, sh

EDITORS = ("Code Helper", "Cursor", "idea", "intellij", "Electron", "Code")
SELF_WAIT_S = 1800                                      # how long --detach waits for its session to go idle


def gh_json(*args):
    rc, so, _ = sh("gh", *args)
    try:
        return json.loads(so) if rc == 0 and so else None
    except json.JSONDecodeError:
        return None


def is_claude(command):
    """Claude Code shows up as `claude` or as its bare version number (e.g. 2.1.310)."""
    return command == "claude" or re.fullmatch(r"\d+\.\d+\.\d+", command) is not None


def is_editor(command):
    return any(e in command for e in EDITORS)


def scan(hub, path, what="all"):
    """The runtime resources of a worktree, matched by path (never by name):
    herdr   [id, label, agent_status]     workspaces whose checkout is the worktree
    compose [project, count, states]      docker compose projects started from inside it
    proc    [pid, command]                processes whose working directory is inside it"""
    w = str(Path(path).resolve()) if Path(path).exists() else str(path)
    found = {"herdr": [], "compose": [], "proc": []}
    if what in ("all", "herdr") and hub.herdr_running():
        for ws in (hub.herdr_try("workspace", "list")[0] or {}).get("workspaces", []):
            if (ws.get("worktree") or {}).get("checkout_path") == w:
                found["herdr"].append([ws["workspace_id"], ws.get("label") or "", str(ws.get("agent_status"))])
    if what in ("all", "compose"):
        projects = {}
        fmt = '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}\t{{.State}}'
        for line in out("docker", "ps", "-a", "--format", fmt).splitlines():
            project, wd, state = (line.split("\t") + ["", ""])[:3]
            if project and (wd == w or wd.startswith(w + os.sep)):
                projects.setdefault(project, []).append(state)
        found["compose"] = [[p, str(len(s)), ",".join(s)] for p, s in projects.items()]
    if what in ("all", "proc"):
        pid = cmd = None
        for line in out("lsof", "-a", "-d", "cwd", "-Fpcn").splitlines():
            kind, value = line[:1], line[1:]
            if kind == "p":
                pid = value
            elif kind == "c":
                cmd = value
            elif kind == "n" and (value == w or value.startswith(w + os.sep)):
                found["proc"].append([pid, cmd])
    return found


class Item:
    def __init__(self, hub, spec):
        if Path(spec).is_absolute() and Path(spec).resolve().is_relative_to(hub.work):
            spec = str(Path(spec).resolve().relative_to(hub.work))
        base, _, item = spec.strip("/").removeprefix("work/").partition("/")
        if not base or not item:
            sys.exit("usage: theflow teardown --check|--run|--detach <base>/<ITEM-ID> [options]")
        self.hub, self.repo, self.item = hub, base, item
        self.w, self.b = hub.work / base / item, hub.base_path(base)
        self.note = hub.items / base / f"{item}.md"
        self.meta = read_note(self.note) if self.note.exists() else {}
        self.label = self.meta.get("label") or f"{base}/{item}"
        self.agent = self.meta.get("agent")
        self.review = self.meta.get("kind") == "review"
        self.talk = self.meta.get("kind") == "talk"
        self.gh = hub.github_repo(base) if (hub.bases / base).exists() else None
        self.merge = hub.merge_mode(base) if (hub.bases / base).exists() else "pr"
        self.default = hub.default_branch(base) if (hub.bases / base).exists() else "main"
        self.noise = tuple(hub.config["noise"])

    # ---- inventory ---------------------------------------------------------------------------------------------

    def agent_status(self):
        """(agent_status, pane_id, tab_id) of the item's agent, all None when it is not running."""
        if not self.agent:
            return None, None, None
        res, err = self.hub.herdr_try("agent", "get", self.agent)
        a = (res or {}).get("agent") or {}
        return a.get("agent_status"), a.get("pane_id"), a.get("tab_id")

    def inventory(self):
        w, inv = str(self.w), {"pending": []}
        pend = inv["pending"].append
        if self.talk:
            inv["exists"] = self.note.exists()
            inv["agent_status"], inv["agent_pane"], _ = self.agent_status()
            return inv
        inv["exists"] = self.w.exists()
        if not inv["exists"]:
            return inv
        sh("git", "-C", w, "fetch", "-q", "origin")
        status = out("git", "-C", w, "status", "--porcelain", "--untracked-files=all").splitlines()
        links = tuple(self.hub.base_config(self.repo).get("worktree_links", []))
        is_noise = lambda s: s.startswith("?? ") and (s[3:].startswith(self.noise) or s[3:] in links)
        inv["noise"] = [s for s in status if is_noise(s)]
        inv["dirty"] = [s for s in status if not is_noise(s)]
        if inv["dirty"]:
            pend(f"uncommitted or untracked: {', '.join(s[3:] for s in inv['dirty'][:8])}"
                 + (f" (+{len(inv['dirty']) - 8} more)" if len(inv["dirty"]) > 8 else ""))
        inv["head"] = out("git", "-C", w, "rev-parse", "HEAD")
        inv["branch"] = out("git", "-C", w, "branch", "--show-current")
        inv["scan"] = scan(self.hub, self.w)
        inv["agent_status"], inv["agent_pane"], _ = self.agent_status()
        herdr_agents = sum(1 for h in inv["scan"]["herdr"] if h[2] not in ("", "none", "None", "null"))
        claudes = [p for p in inv["scan"]["proc"] if is_claude(p[1])]
        if len(claudes) > herdr_agents:
            pend(f"a Claude session outside herdr runs in the worktree (pid {', '.join(p[0] for p in claudes)})")
        if self.review:
            self.review_inventory(inv, pend)
        else:
            self.work_inventory(inv, pend)
        return inv

    def review_inventory(self, inv, pend):
        prs = [p for p in self.meta.get("pr", "").split(",") if p.strip()]
        n = prs[-1].rstrip("/").rsplit("/", 1)[-1] if prs else ""
        inv["branches"], inv["stashes"], inv["prs"], inv["stacked"], inv["remote"] = [], [], {}, [], []
        if not n.isdigit() or not self.gh:
            return
        sh("git", "-C", str(self.w), "fetch", "-q", "origin", f"pull/{n}/head")
        local = out("git", "-C", str(self.w), "rev-list", "HEAD", "^FETCH_HEAD")
        if local:
            pend(f"{len(local.splitlines())} local commit(s) on the detached HEAD")
        pr = gh_json("pr", "view", n, "-R", self.gh, "--json", "state,url,reviews") or {}
        me = out("gh", "api", "user", "-q", ".login")
        inv["prs"] = {"review": [pr]} if pr else {}
        if pr.get("state") == "OPEN" and not any((r.get("author") or {}).get("login") == me for r in pr.get("reviews", [])):
            pend(f"{pr.get('url')} is open and has no review from {me or 'you'}: the draft was never posted")

    def landed(self, br):
        """Whether a branch's tip is already in the default branch (local or origin): a local merge."""
        return any(sh("git", "-C", str(self.b), "merge-base", "--is-ancestor", f"refs/heads/{br}", ref)[0] == 0
                   for ref in (f"refs/heads/{self.default}", f"origin/{self.default}"))

    def work_inventory(self, inv, pend):
        w, b = str(self.w), str(self.b)
        names = {inv["branch"]}   # the current branch plus any the worktree switched between
        for move in out("git", "-C", w, "reflog", "--format=%gs").splitlines():
            hit = re.match(r"checkout: moving from (\S+) to (\S+)$", move)
            if hit:
                names |= set(hit.groups())
        names.discard(self.default)
        inv["branches"] = sorted(n for n in names if n and sh("git", "-C", b, "show-ref", "-q", "--verify",
                                                             f"refs/heads/{n}")[0] == 0)
        stashes = out("git", "-C", w, "stash", "list", "--format=%H%x09%gs").splitlines()
        inv["stashes"] = [s.split("\t", 1) for s in stashes
                          if any(re.search(rf"\bOn {re.escape(br)}:", s) for br in inv["branches"])]
        for sha, subject in inv["stashes"]:
            pend(f"stash {sha[:9]} {subject}")
        inv["prs"], inv["stacked"], inv["remote"], inv["unmerged"] = {}, [], [], []
        for br in inv["branches"]:
            tip = out("git", "-C", b, "rev-parse", f"refs/heads/{br}")
            ahead = out("git", "-C", b, "rev-list", "--count", f"origin/{self.default}..refs/heads/{br}")
            prs = (gh_json("pr", "list", "-R", self.gh, "--head", br, "--state", "all",
                           "--json", "number,state,url,headRefOid,isDraft") or []) if self.gh else []
            inv["prs"][br] = prs
            stacked = (gh_json("pr", "list", "-R", self.gh, "--base", br, "--state", "open",
                               "--json", "number,url") or []) if self.gh else []
            inv["stacked"] += [(br, p["url"]) for p in stacked]
            for p in stacked:
                pend(f"open PR stacked on {br}: {p['url']}")
            if out("git", "-C", b, "ls-remote", "--heads", "origin", br):
                inv["remote"].append(br)
            merged = [p for p in prs if p["state"] == "MERGED"]
            opened = [p for p in prs if p["state"] == "OPEN"]
            for p in opened:
                pend(f"PR {'draft' if p.get('isDraft') else 'open'}: {p['url']}")
            if merged and not opened:
                if not any(p["headRefOid"] == tip for p in merged):
                    pend(f"{br} has commits after its merged PR {merged[0]['url']} (local {tip[:9]})")
                continue
            if opened or ahead in ("", "0") or self.landed(br):
                continue
            closed = [p for p in prs if p["state"] == "CLOSED"]
            if closed:
                pend(f"PR closed without merge: {closed[0]['url']} ({br}, {ahead} commit(s))")
            elif self.merge == "local":
                inv["unmerged"].append(br)
                pend(f"{br}: {ahead} commit(s) not merged into {self.default} (--merge fast-forwards the base)")
            else:
                upstream = out("git", "-C", b, "rev-parse", "--abbrev-ref", f"{br}@{{u}}")
                unpushed = out("git", "-C", b, "rev-list", "--count", f"{upstream}..refs/heads/{br}") if upstream else ""
                if br not in inv["remote"]:
                    pend(f"{br}: {ahead} commit(s), no remote branch and no PR")
                elif unpushed not in ("", "0"):
                    pend(f"{br}: {unpushed} unpushed commit(s), no PR")
                else:
                    pend(f"{br}: {ahead} commit(s) pushed but no PR")

    def report_check(self, inv):
        print(f"item     {self.repo}/{self.item}  {self.label}  ({self.meta.get('kind') or 'code'}, merge {self.merge})")
        if self.talk:
            print(f"agent    {self.agent or '-'} {inv['agent_status'] or 'not running'}"
                  + (f" in pane {inv['agent_pane']}" if inv["agent_pane"] else ""))
            print("nothing pending (talk item: no worktree)")
            return
        if not inv["exists"]:
            print(f"worktree {self.w} is gone")
            return
        print(f"head     {inv['branch'] or '(detached)'} {inv['head'][:9]}")
        print(f"agent    {self.agent or '-'} {inv['agent_status'] or 'not running'}")
        for br, prs in inv["prs"].items():
            for p in prs:
                print(f"pr       {br}: {p.get('state')} {p.get('url')}")
        for br in inv.get("branches", []):
            print(f"branch   {br}{'  (remote exists)' if br in inv['remote'] else ''}"
                  f"{'  (in ' + self.default + ')' if self.landed(br) else ''}")
        for k in ("herdr", "compose", "proc"):
            for r in inv["scan"][k]:
                print(f"{k:<8} {' '.join(r)}")
        if inv["noise"]:
            print(f"noise    {len(inv['noise'])} untracked noise or linked file(s)")
        sib = self.siblings()
        if sib:
            print(f"siblings {', '.join(sib)} (same ITEM-ID, not touched)")
        print("PENDING" if inv["pending"] else "nothing pending")
        for p in inv["pending"]:
            print(f"  - {p}")

    def siblings(self):
        return [f"work/{p.parent.name}/{p.name}" for p in self.hub.work.glob(f"*/{self.item}") if p != self.w]

    # ---- teardown ----------------------------------------------------------------------------------------------

    def wait_for_self(self, pane):
        """Wait until the session that started --detach has ended its turn."""
        deadline = time.time() + SELF_WAIT_S
        while time.time() < deadline:
            self.hub.herdr_try("agent", "wait", pane, "--timeout", "120000")
            res, err = self.hub.herdr_try("agent", "get", pane)
            state = ((res or {}).get("agent") or {}).get("agent_status")
            if err or state in (None, "idle", "done"):
                time.sleep(3)
                return True
            time.sleep(5)
        return False

    def merge_into_base(self, inv, log, left):
        """Fast-forward the base's checkout to each unmerged branch: only on its default branch and clean."""
        b = str(self.b)
        current = out("git", "-C", b, "branch", "--show-current")
        dirty = out("git", "-C", b, "status", "--porcelain", "--untracked-files=no")
        for br in inv.get("unmerged", []):
            if current != self.default or dirty:
                left.append(f"{br} not merged: the base checkout is {'not on ' + self.default if current != self.default else 'dirty'}")
                continue
            rc, _, err = sh("git", "-C", b, "merge", "--ff-only", br)
            (log if rc == 0 else left).append(f"{br} fast-forwarded into {self.default}" if rc == 0
                                              else f"{br} not merged (no fast-forward; rebase it on {self.default}): {err[:120]}")

    def run(self, opts, by, self_pane=None):
        log, left = [], []
        if self.meta.get("status") not in ("active", "wrapping", None):
            return self.finish(by, opts, [], [f"note status is {self.meta.get('status')}: nothing removed"])
        if self_pane and not self.wait_for_self(self_pane):
            return self.finish(by, opts, [], [f"its session stayed busy for {SELF_WAIT_S // 60} min: nothing removed"])
        if self.talk:
            return self.run_talk(opts, by, self_pane)
        inv = self.inventory()
        if not inv["exists"]:
            return self.finish(by, opts, ["worktree already gone"], [])
        if inv["agent_status"] in ("working", "blocked") and inv["agent_pane"] != self_pane:
            return self.finish(by, opts, [], [f"agent {self.agent} is {inv['agent_status']}: nothing removed"],
                               removed=False)
        if "## Outcome" not in self.note.read_text() and not opts["cancelled"]:
            return self.finish(by, opts, [], ["the note has no ## Outcome yet: nothing removed"], removed=False)
        if opts["merge"] and inv.get("unmerged"):
            self.merge_into_base(inv, log, left)
            if left:
                return self.finish(by, opts, log, left, removed=False)
            inv = self.inventory()
        if inv["pending"] and not opts["discard"]:
            return self.finish(by, opts, log, ["pending work and no --discard: nothing removed"] + inv["pending"],
                               removed=False)
        set_note_field(self.note, "status", "wrapping")

        for project, *_ in inv["scan"]["compose"]:
            rc, _, err = sh("docker", "compose", "-p", project, "down", "--volumes", "--remove-orphans")
            images = [i for i in out("docker", "images", "--format", "{{.Repository}}:{{.Tag}}").splitlines()
                      if i.startswith(f"{project}-")]
            if images:
                sh("docker", "rmi", *images)
            (log if rc == 0 else left).append(f"compose {project} down" + ("" if rc == 0 else f" failed: {err[:120]}"))
        for ws, label, _ in inv["scan"]["herdr"]:
            _, err = self.hub.herdr_try("workspace", "close", ws)
            (left if err else log).append(f"herdr workspace {ws} not closed: {err[:120]}" if err
                                          else f"herdr workspace {ws} ({label}) closed")
        time.sleep(2)
        self.stop_processes(log, left)

        force = ["--force"] if opts["discard"] or inv["noise"] else []
        rc, _, err = sh("git", "-C", str(self.b), "worktree", "remove", *force, str(self.w))
        if rc != 0 and self.w.exists() and (opts["discard"] or not inv["pending"]):
            shutil.rmtree(self.w, ignore_errors=True)
        sh("git", "-C", str(self.b), "worktree", "prune")
        log.append("worktree removed")
        for br in inv["branches"]:
            rc, _, err = sh("git", "-C", str(self.b), "branch", "-D", br)
            (log if rc == 0 else left).append(f"branch {br} deleted" if rc == 0 else f"branch {br} kept: {err[:120]}")
        for sha in opts["drop_stash"]:
            refs = out("git", "-C", str(self.b), "stash", "list", "--format=%H").splitlines()
            i = next((i for i, r in enumerate(refs) if r.startswith(sha)), None)
            if i is not None:
                sh("git", "-C", str(self.b), "stash", "drop", f"stash@{{{i}}}")
                log.append(f"stash {sha[:9]} dropped")
        if self.review:
            draft = self.hub.items / self.repo / "drafts" / f"{self.item}.md"
            if draft.exists():
                draft.unlink()
                log.append(f"draft {draft.relative_to(self.hub.root)} deleted")
        else:
            self.remote_branches(inv, opts, log, left)
        self.resync(log, left)
        left += self.verify(inv)
        return self.finish(by, opts, log, left)

    def run_talk(self, opts, by, self_pane):
        """A talk item holds nothing but its agent's pane (or tab) in the base workspace: close it."""
        status, pane, tab = self.agent_status()
        if status in ("working", "blocked") and pane != self_pane:
            return self.finish(by, opts, [], [f"agent {self.agent} is {status}: nothing removed"], removed=False)
        if "## Outcome" not in self.note.read_text() and not opts["cancelled"]:
            return self.finish(by, opts, [], ["the note has no ## Outcome yet: nothing removed"], removed=False)
        set_note_field(self.note, "status", "wrapping")
        log, left = [], []
        tabs = (self.hub.herdr_try("tab", "list")[0] or {}).get("tabs", [])
        tab_label = next((t.get("label") for t in tabs if t["tab_id"] == tab), "")
        if tab and tab_label == f"{self.hub.prefix['talk']}{self.label}":
            _, err = self.hub.herdr_try("tab", "close", tab)
            (left if err else log).append(f"herdr tab {tab} not closed: {err[:120]}" if err
                                          else f"herdr tab {tab} ({tab_label}) closed")
        elif pane:
            _, err = self.hub.herdr_try("pane", "close", pane)
            (left if err else log).append(f"herdr pane {pane} not closed: {err[:120]}" if err
                                          else f"herdr pane {pane} ({self.agent}) closed")
        else:
            log.append("no agent pane was open")
        time.sleep(2)
        if self.agent_status()[1]:
            left.append(f"agent {self.agent} still running")
        return self.finish(by, opts, log, left, removed=not left)

    def stop_processes(self, log, left):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            victims = [(int(pid), cmd) for pid, cmd in scan(self.hub, self.w, "proc")["proc"]
                       if not is_editor(cmd) and not is_claude(cmd) and int(pid) != os.getpid()]
            for pid, cmd in victims:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            if victims:
                log.append(f"{'stopped' if sig == signal.SIGTERM else 'killed'} {', '.join(c for _, c in victims)}")
                time.sleep(3)
        for pid, cmd in scan(self.hub, self.w, "proc")["proc"]:
            if is_editor(cmd) or is_claude(cmd):
                left.append(f"{cmd} (pid {pid}) still has the worktree open: close that window or session")

    def remote_branches(self, inv, opts, log, left):
        stacked_on = {br for br, _ in inv["stacked"]}
        for br in inv["branches"]:
            prs = inv["prs"].get(br, [])
            opened = [p for p in prs if p["state"] == "OPEN"]
            if opts["close_pr"]:
                for p in opened:
                    rc, _, err = sh("gh", "pr", "close", p["url"], "--delete-branch")
                    (log if rc == 0 else left).append(f"closed {p['url']}" if rc == 0 else f"{p['url']} not closed: {err[:120]}")
                if opened:
                    continue
            if br not in inv["remote"] or opened:
                continue
            if br in stacked_on:
                left.append(f"origin/{br} kept: an open PR is stacked on it")
                continue
            if any(p["state"] == "MERGED" for p in prs) or opts["delete_remote"]:
                rc, _, err = sh("git", "-C", str(self.b), "push", "origin", "--delete", br)
                (log if rc == 0 else left).append(f"origin/{br} deleted" if rc == 0 else f"origin/{br} kept: {err[:120]}")
            else:
                left.append(f"origin/{br} kept (not merged through a PR; --delete-remote removes it)")

    def resync(self, log, left):
        """A base the hub owns goes back to origin/<default>; a linked base is your own checkout: only fetched."""
        b, d = str(self.b), self.default
        owned = not self.hub.is_linked(self.repo)
        steps = [("fetch", "--prune", "-q", "origin")]
        if owned:
            steps += [("checkout", "-q", d), ("reset", "-q", "--hard", f"origin/{d}"), ("clean", "-fdq")]
        if not out("git", "-C", b, "remote"):
            return
        for step in steps:
            rc, _, err = sh("git", "-C", b, *step)
            if rc != 0:
                left.append(f"base {self.repo} not resynced ({step[0]}): {err[:120]}")
                return
        log.append(f"base {self.repo} resynced to origin/{d}" if owned else f"base {self.repo} fetched")

    def verify(self, inv):
        left = []
        found = scan(self.hub, self.w)
        left += [f"herdr workspace {h[0]} still open" for h in found["herdr"]]
        left += [f"compose {c[0]} still has {c[1]} container(s)" for c in found["compose"]]
        left += [f"process {p[1]} (pid {p[0]})" for p in found["proc"] if not (is_editor(p[1]) or is_claude(p[1]))]
        if str(self.w) in out("git", "-C", str(self.b), "worktree", "list"):
            left.append("worktree still registered")
        if self.w.exists():
            left.append(f"folder {self.w} still there")
        for br in inv.get("branches", []):
            if out("git", "-C", str(self.b), "branch", "--list", br):
                left.append(f"branch {br} still there")
        return left

    # ---- report ------------------------------------------------------------------------------------------------

    def outcome(self):
        """The last ## Outcome section of the note, up to the next heading or teardown line."""
        text = self.note.read_text() if self.note.exists() else ""
        found = re.findall(r"^## Outcome[^\n]*\n(.*?)(?=^## |^Teardown |\Z)", text, re.S | re.M)
        return found[-1].strip() if found else "(no outcome written)"

    def finish(self, by, opts, log, left, removed=None):
        now = datetime.datetime.now()
        if removed is None:
            removed = not self.w.exists() and not any(x.startswith("worktree still") for x in left) and not self.talk
        verdict = ("cancelled" if opts["cancelled"] else "done") if removed else "NOT wrapped up"
        teardown = (f"Teardown {now:%Y-%m-%d %H:%M} ({by}): " + "; ".join(log or ["nothing done"])
                    + (f". LEFT: {'; '.join(left)}" if left else ". Nothing left."))
        if not removed and not log and by != "self":
            print(teardown)   # a refusal in front of the user: nothing to record
            return 1
        outcome, note_path = self.outcome(), self.note
        if self.note.exists():
            with self.note.open("a") as f:
                f.write(f"\n{teardown}\n")
            if removed:
                set_note_field(self.note, "status", verdict)
                done = self.hub.items / self.repo / "done"
                done.mkdir(exist_ok=True)
                note_path = done / self.note.name
                self.note.rename(note_path)
        sib = self.siblings()
        entry = (f"\n## {now:%Y-%m-%d %H:%M} · {self.label} · {verdict} ({by})\n"
                 + (f"{outcome}\n" if removed else "")
                 + f"{teardown}\n"
                 + (f"Still open with the same ID: {', '.join(sib)}\n" if sib else "")
                 + f"Note: {note_path.relative_to(self.hub.root)}\n")
        if not self.hub.inbox.exists():
            self.hub.inbox.parent.mkdir(parents=True, exist_ok=True)
            self.hub.inbox.write_text(f"# {self.hub.config['name']} inbox: closed items, newest last\n")
        with self.hub.inbox.open("a") as f:
            f.write(entry)
        print(entry.strip())
        summary = f"{verdict}; " + ("teardown clean" if not left else f"{len(left)} thing(s) left")
        self.nudge(by, verdict, outcome if removed else "", teardown, sib)
        self.hub.herdr_try("notification", "show", f"{self.hub.config['name']}: {self.label} {verdict}",
                           "--body", summary, "--sound", "done" if not left else "request")
        return 0 if removed and not left else 1

    def nudge(self, by, verdict, outcome, teardown, sib):
        """FYI to the session that opened the item, only if it is alive, idle, and not the one running this."""
        opener = self.meta.get("opened_by")
        if not opener or opener == os.environ.get("CLAUDE_CODE_SESSION_ID"):
            return
        agents = (self.hub.herdr_try("agent", "list")[0] or {}).get("agents", [])
        hub = next((a for a in agents if (a.get("agent_session") or {}).get("value") == opener), None)
        if not hub or hub.get("agent_status") not in ("idle", "done"):
            return
        outcome = " ".join(outcome.split())[:600] or "(none)"
        text = (f"[theflow FYI from teardown] {self.label} ({self.repo}/{self.item}), which this session opened, "
                f"was wrapped up by {'its own session' if by == 'self' else 'another session'}: {verdict}. "
                f"Outcome: {outcome} {teardown}"
                + (f" Still open with the same ID: {', '.join(sib)}." if sib else "")
                + " No action needed: acknowledge in one line and do not run any tools.")
        self.hub.herdr_try("agent", "prompt", hub["pane_id"], text)


def parse(argv):
    opts = {"merge": False, "discard": False, "delete_remote": False, "close_pr": False, "cancelled": False,
            "drop_stash": []}
    mode, spec, i = None, None, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--check", "--run", "--detach"):
            mode = a[2:]
        elif a == "--drop-stash" and i + 1 < len(argv):
            opts["drop_stash"].append(argv[i + 1])
            i += 1
        elif a.startswith("--") and a[2:].replace("-", "_") in opts:
            opts[a[2:].replace("-", "_")] = True
        elif not a.startswith("--") and spec is None:
            spec = a
        else:
            sys.exit(f"unknown argument {a}\n\n{__doc__}")
        i += 1
    if not mode or not spec:
        sys.exit(__doc__)
    return mode, spec, opts


def detach(item, argv):
    """Double-fork into a new session at the hub root, logging to items/.logs/; the caller returns at once."""
    logs = item.hub.logs
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{item.repo}-{item.item}-teardown.log"
    pane = os.environ.get("HERDR_PANE_ID")
    if os.fork():
        print(f"teardown of {item.repo}/{item.item} starts once this session's turn ends "
              f"(log {log.relative_to(item.hub.root)})")
        return 0
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir(item.hub.root)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0), os.dup2(fd, 1), os.dup2(fd, 2)
    print(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} detached teardown, waiting for pane {pane}", flush=True)
    _, _, opts = parse(argv)
    rc = item.run(opts, "self", self_pane=pane)
    sys.stdout.flush(), sys.stderr.flush()   # os._exit skips the buffers
    os._exit(rc)


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    if argv[0] == "--scan":   # theflow scan <path> [herdr|compose|proc]
        hub = flowlib.Hub()
        for kind, rows in scan(hub, Path(argv[1]).absolute(), argv[2] if len(argv) > 2 else "all").items():
            for r in rows:
                print("\t".join([kind, *r]))
        return
    mode, spec, opts = parse(argv)
    hub = flowlib.Hub()
    item = Item(hub, spec)
    if not item.note.exists() and mode != "check":
        sys.exit(f"no active note {item.note.relative_to(hub.root)}: write the outcome there first")
    if mode == "check":
        item.report_check(item.inventory())
    elif mode == "run":
        me = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if Path.cwd().resolve().is_relative_to(item.w.resolve()) or (me and me == item.meta.get("claude_session")):
            sys.exit("this session is the item's own: use --detach")
        sys.exit(item.run(opts, "hub" if Path.cwd().resolve() == hub.root else "other session"))
    else:
        sys.exit(detach(item, argv))


if __name__ == "__main__":
    main()
