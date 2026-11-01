"""Talk items get a tab of their own, never a split: theflow sync and the talk teardown against a fake herdr.

Run from the repo root: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import flowlib    # noqa: E402
import sync       # noqa: E402
import teardown   # noqa: E402


class FakeHerdr:
    """Just enough of herdr's socket API: workspaces, tabs, panes and agent names, with every call recorded."""

    def __init__(self, base_path):
        self.base, self.calls, self.n = str(base_path), [], 0
        self.workspaces = [{"workspace_id": "w1", "label": "base:x"}]
        self.tabs, self.panes, self.names = [], [], {}

    def tab(self, label, *panes, ws="w1"):
        """A tab with panes, each "shell" or "claude:<agent name>", in the base workspace unless ws says otherwise."""
        tab_id = f"{ws}:t{len(self.tabs) + 1}"
        self.tabs.append({"tab_id": tab_id, "workspace_id": ws, "label": label})
        return [self.pane(tab_id, kind) for kind in panes]

    def pane(self, tab_id, kind="shell"):
        self.n += 1
        ws = tab_id.split(":")[0]
        pane_id = f"{ws}:p{self.n}"
        claude = kind.startswith("claude")
        self.panes.append({"pane_id": pane_id, "tab_id": tab_id, "workspace_id": ws, "cwd": self.base,
                           "agent": "claude" if claude else None, "agent_status": "idle" if claude else None})
        if claude and ":" in kind:
            self.names[pane_id] = kind.split(":", 1)[1]
        return pane_id

    def tab_of(self, pane_id):
        return next(p["tab_id"] for p in self.panes if p["pane_id"] == pane_id)

    def label_of(self, tab_id):
        return next((t["label"] for t in self.tabs if t["tab_id"] == tab_id), None)

    def in_tab(self, tab_id):
        return [p["pane_id"] for p in self.panes if p["tab_id"] == tab_id]

    def opt(self, args, name):
        return args[args.index(name) + 1] if name in args else None

    def __call__(self, *args):
        self.calls.append(args)
        cmd = args[:2]
        if cmd == ("pane", "list"):
            return {"panes": [dict(p) for p in self.panes]}, None
        if cmd == ("agent", "list"):
            return {"agents": [{"pane_id": p, "name": n} for p, n in self.names.items()]}, None
        if cmd == ("workspace", "list"):
            return {"workspaces": self.workspaces}, None
        if cmd == ("tab", "list"):
            return {"tabs": [dict(t) for t in self.tabs]}, None
        if cmd == ("agent", "rename"):
            self.names[args[2]] = args[3]
            return {}, None
        if cmd == ("agent", "get"):
            pane = next((p for p in self.panes if self.names.get(p["pane_id"]) == args[2]), None)
            return ({"agent": pane} if pane else None), (None if pane else "agent_not_found: no such agent")
        if cmd == ("agent", "start"):
            pane = next(p for p in self.panes if p["pane_id"] == self.opt(args, "--pane"))
            pane.update(agent="claude", agent_status="idle")
            self.names[pane["pane_id"]] = args[2]
            return {}, None
        if cmd == ("tab", "create"):
            tab_id = f"{self.opt(args, '--workspace')}:t{len(self.tabs) + 1}"
            self.tabs.append({"tab_id": tab_id, "workspace_id": self.opt(args, "--workspace"),
                              "label": self.opt(args, "--label") or str(len(self.tabs) + 1)})
            return {"tab": {"tab_id": tab_id}, "root_pane": {"pane_id": self.pane(tab_id)}}, None
        if cmd == ("tab", "rename"):
            next(t for t in self.tabs if t["tab_id"] == args[2])["label"] = args[3]
            return {}, None
        if cmd == ("tab", "close"):
            self.tabs = [t for t in self.tabs if t["tab_id"] != args[2]]
            self.panes = [p for p in self.panes if p["tab_id"] != args[2]]
            return {}, None
        if cmd == ("pane", "close"):
            self.panes = [p for p in self.panes if p["pane_id"] != args[2]]
            return {}, None
        if cmd == ("pane", "move") and "--new-tab" in args:
            old = self.tab_of(args[2])
            tab_id = f"{self.opt(args, '--workspace')}:t{len(self.tabs) + 1}"
            self.tabs.append({"tab_id": tab_id, "workspace_id": self.opt(args, "--workspace"),
                              "label": self.opt(args, "--label") or str(len(self.tabs) + 1)})
            next(p for p in self.panes if p["pane_id"] == args[2]).update(tab_id=tab_id)
            if not self.in_tab(old):   # herdr closes a tab left empty
                self.tabs = [t for t in self.tabs if t["tab_id"] != old]
            return {"move_result": {"changed": True, "created_tab": {"tab_id": tab_id}}}, None
        if cmd == ("pane", "split"):
            raise AssertionError(f"a talk item must never split a pane: {args}")
        return {}, None


class TalkTabs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / "hub" / "bases").mkdir(parents=True)
        (root / "folder").mkdir()
        (root / "home").mkdir()
        (root / "hub" / "bases" / "x").symlink_to(root / "folder")
        (root / "hub" / "flow.json").write_text(json.dumps({"name": "t", "claude_home": str(root / "home")}))
        self.note = root / "hub" / "items" / "x" / "probe.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text("---\nitem: probe\nrepo: x\nkind: talk\nlabel: probe\nagent: probe\ncreated: 2026-11-01\n"
                             "status: active\n---\nA test item.\n\n## Notes\n")
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.hub = flowlib.Hub(root / "hub")
        self.path = self.hub.base_path("x")
        self.herdr = FakeHerdr(self.path)
        self.hub.herdr_try = self.herdr

    def sync(self, start=None):
        s = sync.Sync(self.hub, start=start)
        with mock.patch("sys.stdout"):
            s.talks("x", self.path, "w1")

    def test_split_in_the_first_tab_moves_into_a_tab_of_its_own(self):
        shell, claude = self.herdr.tab("1", "shell", "claude:probe")
        self.sync()
        tab = self.herdr.tab_of(claude)
        self.assertEqual(self.herdr.label_of(tab), "talk:probe")
        self.assertEqual(self.herdr.in_tab(tab), [claude])
        self.assertEqual(self.herdr.in_tab(self.herdr.tab_of(shell)), [shell])

    def test_a_shared_later_tab_is_left_its_name(self):
        self.herdr.tab("1", "shell")
        shell, claude = self.herdr.tab("2", "shell", "claude:probe")
        self.sync()
        self.assertEqual(self.herdr.label_of(self.herdr.tab_of(shell)), "2")
        self.assertEqual(self.herdr.label_of(self.herdr.tab_of(claude)), "talk:probe")
        self.assertEqual(self.herdr.in_tab(self.herdr.tab_of(claude)), [claude])

    def test_an_adopted_split_found_by_session_id_moves_too(self):
        _, claude = self.herdr.tab("1", "shell", "claude")
        next(p for p in self.herdr.panes if p["pane_id"] == claude)["agent_session"] = {"value": "sid-1"}
        flowlib.set_note_field(self.note, "claude_session", "sid-1")
        self.sync()
        self.assertEqual(self.herdr.names[claude], "probe")
        self.assertEqual(self.herdr.label_of(self.herdr.tab_of(claude)), "talk:probe")
        self.assertEqual(self.herdr.in_tab(self.herdr.tab_of(claude)), [claude])

    def test_a_split_made_inside_its_own_talk_tab_stays(self):
        self.herdr.tab("1", "shell")
        claude, shell = self.herdr.tab("talk:probe", "claude:probe", "shell")
        self.sync()
        self.assertEqual(self.herdr.in_tab("w1:t2"), [claude, shell])
        self.assertEqual([t["label"] for t in self.herdr.tabs], ["1", "talk:probe"])

    def test_a_busy_agent_is_not_moved(self):
        _, claude = self.herdr.tab("1", "shell", "claude:probe")
        next(p for p in self.herdr.panes if p["pane_id"] == claude)["agent_status"] = "blocked"
        self.sync()
        self.assertEqual(self.herdr.tab_of(claude), "w1:t1")
        self.assertFalse(any(c[:2] == ("pane", "move") for c in self.herdr.calls))

    def test_an_agent_in_another_workspace_gets_its_tab_there(self):
        self.herdr.workspaces.append({"workspace_id": "w9", "label": "hub"})
        self.herdr.tab("1", "shell")
        _, claude = self.herdr.tab("1", "shell", "claude", ws="w9")
        next(p for p in self.herdr.panes if p["pane_id"] == claude)["agent_session"] = {"value": "sid-1"}
        flowlib.set_note_field(self.note, "claude_session", "sid-1")
        self.sync()
        tab = self.herdr.tab_of(claude)
        self.assertTrue(tab.startswith("w9:"))
        self.assertEqual(self.herdr.label_of(tab), "talk:probe")
        self.assertEqual(self.herdr.in_tab(tab), [claude])

    def test_a_lone_agent_in_a_later_tab_gets_the_tab_named(self):
        self.herdr.tab("1", "shell")
        (claude,) = self.herdr.tab("2", "claude:probe")
        self.sync()
        self.assertEqual(self.herdr.label_of(self.herdr.tab_of(claude)), "talk:probe")
        self.assertFalse(any(c[:2] == ("pane", "move") for c in self.herdr.calls))

    def test_a_lone_agent_in_the_first_tab_stays(self):
        (claude,) = self.herdr.tab("1", "claude:probe")
        self.sync()
        self.assertEqual(self.herdr.tab_of(claude), "w1:t1")
        self.assertEqual(self.herdr.label_of("w1:t1"), "1")
        self.assertFalse(any(c[:2] in (("pane", "move"), ("tab", "rename")) for c in self.herdr.calls))

    def test_start_opens_a_tab_never_a_split(self):
        self.herdr.tab("1", "shell")
        self.sync(start="x/probe")
        claude = next(p for p, n in self.herdr.names.items() if n == "probe")
        self.assertEqual(self.herdr.label_of(self.herdr.tab_of(claude)), "talk:probe")
        self.assertEqual(self.herdr.in_tab(self.herdr.tab_of(claude)), [claude])

    def test_start_reuses_an_empty_talk_tab_but_not_a_split_one(self):
        self.herdr.tab("1", "shell")
        (free,) = self.herdr.tab("talk:probe", "shell")
        self.sync(start="x/probe")
        self.assertEqual(self.herdr.names.get(free), "probe")

        self.setUp()
        self.herdr.tab("1", "shell")
        a, b = self.herdr.tab("talk:probe", "shell", "shell")
        self.sync(start="x/probe")
        claude = next(p for p, n in self.herdr.names.items() if n == "probe")
        self.assertNotIn(claude, (a, b))
        self.assertEqual(self.herdr.in_tab(self.herdr.tab_of(claude)), [claude])

    def test_start_skips_a_talk_tab_whose_shell_left_the_base(self):
        self.herdr.tab("1", "shell")
        (moved,) = self.herdr.tab("talk:probe", "shell")
        next(p for p in self.herdr.panes if p["pane_id"] == moved)["cwd"] = "/somewhere/else"
        self.sync(start="x/probe")
        claude = next(p for p, n in self.herdr.names.items() if n == "probe")
        self.assertNotEqual(claude, moved)

    def teardown(self):
        t = teardown.Item(self.hub, "x/probe")
        with mock.patch("sys.stdout"), mock.patch.object(teardown.time, "sleep"):
            t.run_talk({"cancelled": True}, "hub", None)

    def test_teardown_closes_a_talk_tab_of_its_own(self):
        self.herdr.tab("1", "shell")
        (claude,) = self.herdr.tab("talk:probe", "claude:probe")
        self.teardown()
        self.assertEqual([t["label"] for t in self.herdr.tabs], ["1"])

    def test_teardown_leaves_a_shared_tab_and_its_other_panes(self):
        self.herdr.tab("1", "shell")
        shell, claude = self.herdr.tab("talk:probe", "shell", "claude:probe")
        self.teardown()
        self.assertIn("stays: other panes run in it", self.hub.inbox.read_text())
        self.assertEqual(self.herdr.in_tab("w1:t2"), [shell])

    def test_no_talk_setting_left(self):
        self.assertNotIn("talk", flowlib.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
